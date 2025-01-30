import os
import re
from flask import Flask, request, render_template, send_file

# Initialize Flask app
app = Flask(__name__, template_folder="templates")

# Set up paths relative to the MikrotikMigrate folder
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'MikrotikMigrate', 'uploads')
PROCESSED_FOLDER = os.path.join(BASE_DIR, 'MikrotikMigrate', 'processed')
TEMPLATES_FOLDER = os.path.join(BASE_DIR, 'MikrotikMigrate', 'templates')


# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PROCESSED_FOLDER'] = PROCESSED_FOLDER

# Define mappings for configuration migration
interface_mapping = {
    "1036": {
        "ether1": "sfp-sfpplus1",
        "ether2": "sfp-sfpplus2",
        "ether3": "sfp-sfpplus3",
        "ether4": "sfp-sfpplus4",
    },
    "2004": {
        "sfp-sfpplus1": "ether1",
        "sfp-sfpplus2": "ether2",
        "sfp-sfpplus3": "ether3",
    },
}

# OSPF transformation for 2004
def transform_ospf_2004(router_id, lan_network, loopback_network):
    ospf_config = f"""/routing ospf instance
add disabled=no name=default-v2 router-id={router_id}
/routing ospf area
add disabled=no instance=default-v2 name=backbone-v2
/routing ospf interface-template
add area=backbone-v2 cost=10 disabled=no interfaces=loop0 networks={loopback_network} passive priority=1
add area=backbone-v2 cost=10 disabled=no interfaces=lan-bridge networks={lan_network} priority=1
"""
    return ospf_config

# BGP transformation for 2004
def transform_bgp_2004(router_id, as_number, peer_ips):
    bgp_config = f"""/routing bgp template
set default as={as_number} disabled=no multihop=yes output.network=bgp-networks router-id={router_id} routing-table=main
/routing bgp connection
add cisco-vpls-nlri-len-fmt=auto-bits connect=yes listen=yes local.address={router_id} .role=ibgp multihop=yes name=Peer1 remote.address={peer_ips[0]} .as={as_number} .port=179 templates=default
add cisco-vpls-nlri-len-fmt=auto-bits connect=yes listen=yes local.address={router_id} .role=ibgp multihop=yes name=Peer2 remote.address={peer_ips[1]} .as={as_number} .port=179 templates=default
"""
    return bgp_config

# Parse and migrate configuration
def parse_and_migrate(config_content, source_model, target_model):
    router_id = extract_router_id(config_content)
    as_number = extract_as_number(config_content)
    lan_network = extract_lan_network(config_content)
    loopback_network = extract_loopback_network(config_content)
    peer_ips = extract_peer_ips(config_content)

    # Replace interfaces
    for src_iface, tgt_iface in interface_mapping.get(source_model, {}).items():
        config_content = re.sub(rf'\b{src_iface}\b', tgt_iface, config_content)

    # Transform OSPF for 2004
    if target_model == "2004":
        ospf_section = transform_ospf_2004(router_id, lan_network, loopback_network)
        config_content = re.sub(r'/routing ospf[\s\S]*?/routing bgp', ospf_section + '\n/routing bgp', config_content, flags=re.MULTILINE)

    # Transform BGP for 2004
    if target_model == "2004":
        bgp_section = transform_bgp_2004(router_id, as_number, peer_ips)
        config_content = re.sub(r'/routing bgp[\s\S]*?/system', bgp_section + '\n/system', config_content, flags=re.MULTILINE)

    return config_content

# Helper functions
def extract_router_id(config_content):
    """Extract router ID from configuration."""
    match = re.search(r'router-id=(\S+)', config_content)
    return match.group(1) if match else "<dynamic-router-id>"

def extract_as_number(config_content):
    """Extract AS number for BGP."""
    match = re.search(r'as=(\d+)', config_content)
    return match.group(1) if match else "65000"

def extract_lan_network(config_content):
    """Extract LAN bridge network."""
    match = re.search(r'networks=(\S+)/22', config_content)
    return match.group(1) + "/22" if match else "0.0.0.0/22"

def extract_loopback_network(config_content):
    """Extract loopback network."""
    match = re.search(r'networks=(\S+)/32', config_content)
    return match.group(1) + "/32" if match else "0.0.0.0/32"

def extract_peer_ips(config_content):
    """Extract peer IPs for BGP."""
    matches = re.findall(r'remote.address=(\S+)', config_content)
    return matches if matches else ["0.0.0.0", "0.0.0.0"]

@app.route('/')
def index():
    return render_template('Mikrotik.html')  # Ensure it is inside templates/

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file uploaded", 400

    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400

    source_model = request.form.get('source_model')
    target_model = request.form.get('target_model')

    if not source_model or not target_model:
        return "Source or target model not specified", 400

    # Save uploaded file
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(file_path)

    # Read and process the configuration file
    with open(file_path, 'r') as f:
        config_content = f.read()

    # Migrate the configuration
    migrated_content = parse_and_migrate(config_content, source_model, target_model)

    # Save the migrated configuration
    processed_file_path = os.path.join(app.config['PROCESSED_FOLDER'], f"migrated_{file.filename}")
    with open(processed_file_path, 'w') as f:
        f.write(migrated_content)

    return send_file(processed_file_path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
