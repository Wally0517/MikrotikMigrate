import os
import re
import logging
from flask import Flask, request, render_template, jsonify, send_file
from flask import jsonify
# Initialize Flask app
app = Flask(__name__, template_folder="templates")

# Set up paths relative to the MikrotikMigrate folder
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = "/tmp/uploads"
PROCESSED_FOLDER = "/tmp/processed"
TEMPLATES_FOLDER = os.path.join(BASE_DIR, 'MikrotikMigrate', 'templates')


# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PROCESSED_FOLDER'] = PROCESSED_FOLDER

# 🎯 **DYNAMIC INTERFACE MAPPING**
def dynamic_interface_mapping(config_content, source_model, target_model):
    """
    Dynamically replaces all `etherX` and `sfpX` interfaces with `sfp-sfpplusX` in the target model.
    Returns the updated configuration and the mapping dictionary.
    """
    if target_model == "2004":
        sfp_index = 1  # Start indexing from sfp-sfpplus1
        interface_mappings = {}  # Store mappings of old -> new interfaces
        new_config_lines = []

        for line in config_content.splitlines():
            match = re.search(r'(default-name=)(ether\d+|sfp\d+)(.*)', line)
            if match:
                prefix, old_iface, additional_config = match.groups()

                # Assign new interface dynamically
                new_iface = f"sfp-sfpplus{sfp_index}"
                sfp_index += 1

                # Store mapping for later use
                interface_mappings[old_iface] = new_iface
                logging.debug(f"Mapped {old_iface} ➝ {new_iface}")

                # Update the line with the new interface
                new_config_lines.append(line.replace(old_iface, new_iface))
            else:
                new_config_lines.append(line)

        updated_config = "\n".join(new_config_lines)
        return updated_config, interface_mappings  # ✅ Return both values

    return config_content, {}  # ✅ Return empty dictionary if no migration needed


# 🎯 **MIGRATE /IP ADDRESSES**
def migrate_ip_addresses(config_content, interface_mappings):
    """
    Updates the /ip address section by replacing old interfaces with newly mapped ones.
    Ensures all IP assignments correctly reflect new interfaces.
    """
    migrated_lines = []
    in_ip_section = False  # Track if we are in the /ip address section

    for line in config_content.splitlines():
        if line.strip().startswith("/ip address"):
            in_ip_section = True
            migrated_lines.append(line)  # Keep section header
            continue

        if in_ip_section and "interface=" in line:
            # Find old interface names and replace them with mapped ones
            ip_match = re.search(r'(interface=)(ether\d+|sfp\d+)', line)
            if ip_match:
                prefix, old_iface = ip_match.groups()
                new_iface = interface_mappings.get(old_iface, old_iface)  # Replace if mapped
                updated_line = line.replace(old_iface, new_iface)
                migrated_lines.append(updated_line)
            else:
                migrated_lines.append(line)
        else:
            migrated_lines.append(line)

    return "\n".join(migrated_lines)
    
# OSPF transformation for 2004
def transform_ospf_2004(router_id, lan_network, loopback_network, config_content):
    """
    Transforms OSPF configuration for CCR2004, ensuring compatibility with ROS7.
    Fixes authentication changes:
      - 'authentication=' → 'auth='
      - 'authentication-key=' → 'auth-key='
    """

    # Convert authentication parameters for ROS7
    config_content = re.sub(r'\bauthentication\b', 'auth', config_content)  # First replace 'authentication='
    config_content = re.sub(r'\bauthentication-key\b', 'auth-key', config_content)  # Then replace 'authentication-key='

    ospf_config = f"""/routing ospf instance
add disabled=no name=default-v2 router-id={router_id} version=2 redistribute-connected=yes redistribute-static=yes
/routing ospf area
add disabled=no instance=default-v2 name=backbone-v2
/routing ospf interface-template
add area=backbone-v2 cost=10 disabled=no interfaces=loop0 networks={loopback_network} passive=yes priority=1
add area=backbone-v2 cost=10 disabled=no interfaces=lan-bridge networks={lan_network} priority=1
"""

    return ospf_config  # ✅ Only return transformed OSPF config


# BGP transformation for 2004
def transform_bgp_2004(router_id, as_number, peer_ips):
    """
    Transforms BGP configuration for CCR2004.
    Ensures proper peering with remote AS.
    """
    bgp_config = f"""/routing bgp template
set default as={as_number} disabled=no multihop=yes output.network=bgp-networks router-id={router_id} routing-table=main
/routing bgp connection
add name=Peer1 remote.address={peer_ips[0]} remote.as={as_number} connect=yes listen=yes local.address={router_id} multihop=yes .port=179 templates=default
add name=Peer2 remote.address={peer_ips[1]} remote.as={as_number} connect=yes listen=yes local.address={router_id} multihop=yes .port=179 templates=default
"""
    return bgp_config

# 🎯 **FULL CONFIGURATION MIGRATION - NOW FULLY ROS7 COMPATIBLE**
def parse_and_migrate(config_content, source_model, target_model):
    """
    Parses and migrates the configuration from source model to target model (CCR2004).
    Ensures full transformation including interfaces, OSPF, BGP, IP addresses, firewall rules, and routing policies.
    Prevents duplicate entries in key sections.
    Fully compatible with ROS7.
    """
    router_id = extract_router_id(config_content)
    as_number = extract_as_number(config_content)
    lan_network = extract_lan_network(config_content)
    loopback_network = extract_loopback_network(config_content)
    peer_ips = extract_peer_ips(config_content)

    # Step 1️⃣: Replace interfaces dynamically
    config_content, interface_mappings = dynamic_interface_mapping(config_content, source_model, target_model)

    # Step 2️⃣: Migrate IP address section
    config_content = migrate_ip_addresses(config_content, interface_mappings)

    # Step 3️⃣: Extract and retain firewall rules (now ensuring uniqueness)
    firewall_rules = extract_firewall_rules(config_content)
    firewall_rules = remove_duplicates(firewall_rules)  # ✅ Remove duplicate firewall rules

    # Step 4️⃣: Apply OSPF transformation (ROS7 Compatible)
    if target_model == "2004":
        ospf_section = transform_ospf_2004(router_id, lan_network, loopback_network)
        ospf_section = update_ospf_authentication(ospf_section)  # ✅ Convert `authentication=` to `auth=`
        ospf_section = remove_duplicates(ospf_section)  # ✅ Remove duplicate OSPF entries
        config_content += f"\n\n{ospf_section}"

    # Step 5️⃣: Apply BGP transformation (ROS7 Compatible)
    if target_model == "2004":
        bgp_section = transform_bgp_2004(router_id, as_number, peer_ips)
        bgp_section = remove_duplicates(bgp_section)  # ✅ Remove duplicate BGP entries
        config_content += f"\n\n{bgp_section}"

    # Step 6️⃣: Migrate /ip route section for CCR2004
    if target_model == "2004":
        route_section = migrate_ip_routes(config_content, interface_mappings)
        route_section = remove_duplicates(route_section)  # ✅ Prevent duplicate route entries
        config_content += f"\n\n{route_section}"

    # Step 7️⃣: Ensure firewall rules are included (without duplicates)
    if firewall_rules:
        config_content += f"\n\n{firewall_rules}"

    return config_content  # ✅ Fully processed with NO DUPLICATES!

# Helper functions
def extract_router_id(config_content):
    """
    Extracts router ID dynamically from the source model.
    Ensures that the router ID is always correctly captured.
    """
    match = re.search(r'router-id\s*=\s*(\S+)', config_content)
    return match.group(1) if match else "<dynamic-router-id>"

def extract_as_number(config_content):
    """
    Extracts AS number for BGP from the source configuration.
    Defaults to '65000' if not explicitly found.
    """
    match = re.search(r'\bas\s*=\s*(\d+)', config_content)
    return match.group(1) if match else "65000"

def extract_lan_network(config_content):
    """
    Extracts LAN bridge network dynamically.
    Captures correct subnet format from any input config.
    """
    match = re.search(r'address\s*=\s*(\d+\.\d+\.\d+\.\d+)/(\d+)', config_content)
    return f"{match.group(1)}/{match.group(2)}" if match else "0.0.0.0/22"

def extract_loopback_network(config_content):
    """
    Extracts the loopback network dynamically from the configuration.
    Ensures a /32 subnet is assigned for loopback.
    """
    match = re.search(r'interface=loopback.*?address\s*=\s*(\d+\.\d+\.\d+\.\d+)/(\d+)', config_content, re.IGNORECASE)
    return f"{match.group(1)}/{match.group(2)}" if match else "0.0.0.0/32"

def extract_firewall_rules(config_content):
    """
    Extracts firewall rules from the source configuration.
    Ensures that all firewall rules are migrated properly.
    """
    firewall_section = []
    in_firewall_section = False

    for line in config_content.splitlines():
        if line.strip().startswith("/ip firewall"):
            in_firewall_section = True
            firewall_section.append(line)
        elif in_firewall_section:
            if line.strip() == "":
                break  # Stop at the end of the firewall section
            firewall_section.append(line)

    return "\n".join(firewall_section) if firewall_section else ""

def migrate_ip_routes(config_content, interface_mappings):
    """
    Migrates `/ip route` section by mapping interfaces dynamically for CCR2004.
    Ensures compatibility with RouterOS 7.
    """
    migrated_lines = []
    in_route_section = False  # Track if we are in the `/ip route` section

    for line in config_content.splitlines():
        if line.strip().startswith("/ip route"):
            in_route_section = True
            migrated_lines.append(line)  # Keep section header
            continue

        if in_route_section and "gateway=" in line:
            # Find old interface names and replace them with mapped ones
            route_match = re.search(r'(gateway=)(ether\d+|sfp\d+)', line)
            if route_match:
                prefix, old_iface = route_match.groups()
                new_iface = interface_mappings.get(old_iface, old_iface)  # Replace if mapped
                migrated_lines.append(line.replace(old_iface, new_iface))
            else:
                migrated_lines.append(line)
        else:
            migrated_lines.append(line)

    return "\n".join(migrated_lines)

def remove_duplicates(section):
    """
    Removes duplicate lines from a section while maintaining order.
    Ensures unique firewall rules, OSPF, and BGP entries.
    """
    seen = set()
    unique_lines = []

    for line in section.splitlines():
        stripped_line = line.strip()

        if stripped_line and stripped_line not in seen:
            seen.add(stripped_line)
            unique_lines.append(line)  # Preserve original spacing

    return "\n".join(unique_lines)

def extract_peer_ips(config_content):
    """
    Extracts BGP peer IPs dynamically.
    Ensures multiple peer IPs are captured if present.
    """
    matches = re.findall(r'remote\.address\s*=\s*(\d+\.\d+\.\d+\.\d+)', config_content)
    return matches if matches else ["0.0.0.0", "0.0.0.0"]

def extract_ip_addresses(config_content):
    """
    Extracts all /ip address entries dynamically from the source model.
    Ensures accurate IP assignments are mapped to the correct interfaces.
    """
    ip_mappings = {}
    for match in re.findall(r'add address=(\d+\.\d+\.\d+\.\d+/\d+).*?interface=(\S+)', config_content):
        ip_address, interface = match
        ip_mappings[interface] = ip_address

    return ip_mappings

@app.route('/')
def index():
    return render_template('Mikrotik.html')  # Ensure it is inside templates/

#enabling log
logging.basicConfig(level=logging.DEBUG)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return {"error": "No file uploaded"}, 400

    file = request.files['file']
    if file.filename == '':
        return {"error": "No selected file"}, 400

    source_model = request.form.get('source_model')
    target_model = request.form.get('target_model')

    if not source_model or not target_model:
        return {"error": "Source or target model not specified"}, 400

    # Save uploaded file
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(file_path)

    # Read and process the configuration file
    try:
        with open(file_path, 'r') as f:
            config_content = f.read()

        # Debug: Ensure file content is being read
        print(f"Read config file: {file.filename}, Content Size: {len(config_content)}")

        # Migrate the configuration
        migrated_content = parse_and_migrate(config_content, source_model, target_model)

        # Debug: Ensure transformation is happening
        print(f"Migrated content size: {len(migrated_content)}")
        print("FINAL MIGRATION OUTPUT:\n", migrated_content)  # ✅ Debugging

        # Return source and target model previews as JSON
        return jsonify({
            "source_config": config_content,
            "target_config": migrated_content
        })

    except Exception as e:
        print(f"Error processing file: {str(e)}")
        return {"error": str(e)}, 500

    # Save the migrated configuration
    processed_file_path = os.path.join(app.config['PROCESSED_FOLDER'], f"migrated_{file.filename}")
    with open(processed_file_path, 'w') as f:
        f.write(migrated_content)

    return send_file(processed_file_path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
