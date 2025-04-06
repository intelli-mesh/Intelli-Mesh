from flask import Flask, render_template, jsonify, session, request
import subprocess
import re
import time
from functools import wraps
from datetime import datetime, timedelta
from threading import Thread

app = Flask(__name__)
app.secret_key = "your_secure_secret_key_here"
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Add this global variable near the top of the file
last_successful_ping = {
    "wan": {"time": datetime.now(), "value": None},
    "wwan": {"time": datetime.now(), "value": None}
}

# Add these global variables near the top of the file
interface_speeds = {
    "wan": {"rx": 0, "tx": 0},
    "wwan": {"rx": 0, "tx": 0}
}

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    if session.get("authenticated"):
        return render_template('dashboard.html')
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    if request.form.get('username') == 'admin' and request.form.get('password') == 'admin':
        session['authenticated'] = True
        return jsonify({"status": "success", "redirect": "/dashboard"})
    return jsonify({"error": "Invalid credentials"}), 401

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return jsonify({"status": "success"})

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

# System Monitoring Endpoints
@app.route('/system_stats')
@login_required
def system_stats():
    return jsonify({
        "storage": get_storage(),
        "memory": get_memory(),
        "latency": get_latency(),
        "lte_info": get_lte_info(),
        "clients": get_connected_clients(),
        "batman_nodes": get_batman_nodes(),
        "network_speeds": get_interface_speeds(),
        "network_info": get_network_info(),
        "service_status": {
            "selfheal": check_service_status("self_heal"),
            "mwan3": check_service_status("mwan3")
        }
    })

def get_storage():
    try:
        output = subprocess.check_output("df -hT /", shell=True).decode()
        match = re.search(r'(\d+\.?\d*)([GM])\s+(\d+\.?\d*)([GM])\s+(\d+\.?\d*)([GM])\s+(\d+%)', output)
        if match:
            return {
                "total": f"{match.group(1)}{match.group(2)}",
                "used": f"{match.group(3)}{match.group(4)}",
                "available": f"{match.group(5)}{match.group(6)}",
                "percentage": match.group(7)
            }
        return {"error": "Storage data unavailable"}
    except Exception as e:
        return {"error": str(e)}

def get_memory():
    try:
        with open('/proc/meminfo') as f:
            mem = f.read()
        total = round(int(re.search(r'MemTotal:\s+(\d+)', mem).group(1)) / 1e6, 1)
        free = round(int(re.search(r'MemAvailable:\s+(\d+)', mem).group(1)) / 1e6, 1)
        used = round(total - free, 1)
        used_percent = round((used / total) * 100, 1)
        return {"total": total, "free": free, "used": used, "used_percent": used_percent}
    except Exception as e:
        return {"error": str(e)}

def get_latency():
    def ping(interface, interface_name):
        global last_successful_ping
        try:
            output = subprocess.check_output(
                f"ping -c 3 -W 2 -I {interface} 1.1.1.1",
                shell=True,
                stderr=subprocess.STDOUT
            ).decode()
            times = re.findall(r'time=(\d+\.?\d*)', output)
            if times:  # Successful ping
                avg_latency = round(sum(float(t) for t in times) / len(times), 1)
                last_successful_ping[interface_name] = {
                    "time": datetime.now(),
                    "value": avg_latency
                }
                return avg_latency
            elif (datetime.now() - last_successful_ping[interface_name]["time"]) < timedelta(seconds=30):
                # Return last known value if failure is less than 30 seconds old
                return last_successful_ping[interface_name]["value"]
            return None
        except:
            if (datetime.now() - last_successful_ping[interface_name]["time"]) < timedelta(seconds=30):
                # Return last known value if failure is less than 30 seconds old
                return last_successful_ping[interface_name]["value"]
            return None

    return {
        "wan": ping("wan", "wan"),
        "wwan": ping("eth2", "wwan")
    }

def get_lte_info():
    try:
        # Get SIM status
        cpim_output = subprocess.check_output("at /dev/ttyUSB2 AT+CPIN?", shell=True).decode()
        
        # Check if SIM is not present (CME ERROR: 10)
        if "+CME ERROR: 10" in cpim_output:
            return {
                "status": "NO_SIM",
                "provider": "Not Available",
                "technology": "None",
                "signal_strength": 0,
                "signal_percent": 0
            }
        
        sim_status = "READY" if "READY" in cpim_output else "PIN_REQUIRED" if "SIM PIN" in cpim_output else "UNKNOWN"

        # Get network info
        cops = subprocess.check_output("at /dev/ttyUSB2 AT+COPS?", shell=True).decode()
        match = re.search(r'\"(.+?)\",(\d+)', cops)
        tech_map = {'0': '2G', '1': '2G', '2': '3G', '7': '4G', '10': '5G'}

        # Get signal strength
        csq = subprocess.check_output("at /dev/ttyUSB2 AT+CSQ", shell=True).decode()
        signal_match = re.search(r'\+CSQ:\s*(\d+),', csq)
        signal_strength = int(signal_match.group(1)) if signal_match else 0
        signal_percent = round((signal_strength / 31) * 100)  # 31 is max value

        return {
            "status": sim_status,
            "provider": match.group(1) if match else "Unknown",
            "technology": tech_map.get(match.group(2), "UNKNOWN") if match else "Unknown",
            "signal_strength": signal_strength,
            "signal_percent": signal_percent
        }
    except Exception as e:
        # Handle any exceptions (including command failures)
        return {
            "status": "ERROR",
            "provider": "Not Available",
            "technology": "None",
            "signal_strength": 0,
            "signal_percent": 0,
            "error": str(e)
        }

def get_connected_clients():
    try:
        # Get associated MACs
        macs = subprocess.check_output("iwinfo phy0-ap0 assoclist | grep -oE '([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}'", shell=True).decode().split()

        # Get DHCP leases
        with open('/tmp/dhcp.leases') as f:
            leases = [line.split()[1:4] for line in f.readlines()]

        clients = []
        for mac in macs:
            mac_lower = mac.lower()
            for lease in leases:
                if lease[0].lower() == mac_lower:
                    clients.append({"mac": mac, "name": lease[2] if lease[2] != "*" else "Unknown"})
                    break
        return clients
    except Exception as e:
        return {"error": str(e)}

def get_batman_nodes():
    try:
        output = subprocess.check_output("batctl n", shell=True).decode()
        nodes = []
        # Parse the neighbor lines
        for line in output.splitlines():
            # Skip header lines and only process lines with phy1-mesh0
            if 'phy1-mesh0' in line and not line.startswith('[B.A.T.M.A.N.'):
                parts = line.split()
                if len(parts) >= 3:
                    mac = parts[1].strip()  # Get the neighbor MAC
                    # Get latency using batctl p
                    try:
                        ping_output = subprocess.check_output(f"batctl p -c 1 {mac}", shell=True).decode()
                        match = re.search(r'(\d+\.?\d*) ms', ping_output)
                        latency = float(match.group(1)) if match else None
                    except:
                        latency = None
                    
                    nodes.append({
                        "mac": mac,
                        "last_seen": parts[2],
                        "latency": str(latency) if latency is not None else "N/A"
                    })
        return nodes
    except Exception as e:
        return {"error": str(e)}

def speed_monitor():
    global interface_speeds
    while True:
        for interface, key in [("wan", "wan"), ("eth2", "wwan")]:  # eth2 is wwan
            try:
                with open(f"/sys/class/net/{interface}/statistics/rx_bytes") as f:
                    rx1 = int(f.read())
                with open(f"/sys/class/net/{interface}/statistics/tx_bytes") as f:
                    tx1 = int(f.read())
                
                # Reduced sleep time from 1s to 0.2s
                time.sleep(0.2)
                
                with open(f"/sys/class/net/{interface}/statistics/rx_bytes") as f:
                    rx2 = int(f.read())
                with open(f"/sys/class/net/{interface}/statistics/tx_bytes") as f:
                    tx2 = int(f.read())
                
                # Multiply by 5 since we're measuring over 0.2s instead of 1s
                interface_speeds[key] = {
                    "rx": round((rx2 - rx1) * 5 / 1024, 1),  # KB/s
                    "tx": round((tx2 - tx1) * 5 / 1024, 1)   # KB/s
                }
            except Exception as e:
                print(f"DEBUG: Error monitoring {interface} speed: {str(e)}")
                interface_speeds[key] = {"rx": 0, "tx": 0}

# Start the monitoring thread when the app starts
speed_thread = Thread(target=speed_monitor, daemon=True)
speed_thread.start()

def get_interface_speeds():
    global interface_speeds
    return interface_speeds

def check_service_status(service):
    try:
        if service == "mwan3":
            # Check if mwan3 is enabled first
            try:
                subprocess.check_output(f"/etc/init.d/{service} enabled", shell=True, stderr=subprocess.DEVNULL)
            except:
                print(f"DEBUG: mwan3 not enabled")
                return "stopped"
                
            # Check if both interfaces are online
            try:
                mwan3_status = subprocess.check_output("mwan3 status", shell=True).decode()
                wan_online = "interface wan is online" in mwan3_status
                wwan_online = "interface wwan is online" in mwan3_status
                
                # If both interfaces are online, load balancing is running
                status = "running" if wan_online and wwan_online else "stopped"
                print(f"DEBUG: mwan3 status: {status}, wan_online: {wan_online}, wwan_online: {wwan_online}")
                return status
            except Exception as e:
                print(f"DEBUG: Error checking mwan3 status: {str(e)}")
                return "stopped"
        else:
            # Check if service is enabled
            status = subprocess.check_output(f"/etc/init.d/{service} enabled", shell=True, stderr=subprocess.DEVNULL).decode()
            return "running" if status.strip() == "" else "stopped"
    except:
        # If command fails, check if service exists and is enabled
        try:
            subprocess.check_output(f"test -f /etc/init.d/{service}", shell=True)
            return "stopped"
        except:
            return "unknown"

def get_network_info():
    try:
        # Get LAN IP - read directly from UCI config
        lan_ip = subprocess.check_output("uci get network.lan.ipaddr", shell=True, stderr=subprocess.DEVNULL).decode().strip()
    except:
        lan_ip = "Unknown"

    try:
        # Get Node IP - read directly from UCI config
        node_ip = subprocess.check_output("uci get network.default.ipaddr", shell=True, stderr=subprocess.DEVNULL).decode().strip()
    except:
        node_ip = "Unknown"

    try:
        # Get MAC ID from batctl command
        batman_output = subprocess.check_output("batctl n", shell=True, stderr=subprocess.DEVNULL).decode()
        mac_match = re.search(r'MainIF/MAC: phy1-mesh0/([0-9a-fA-F:]{17})', batman_output)
        mac_id = mac_match.group(1) if mac_match else "Unknown"
    except:
        mac_id = "Unknown"

    return {
        "lan_ip": lan_ip,
        "node_ip": node_ip,
        "mac_id": mac_id
    }

# Control Endpoints
@app.route('/control', methods=['POST'])
@login_required
def control():
    command = request.json.get('command')
    valid_commands = {
        'selfheal_enable': '/etc/init.d/self_heal enable && /etc/init.d/self_heal start',
        'selfheal_disable': '/etc/init.d/self_heal disable && /etc/init.d/self_heal stop',
        'loadbalance_enable': 'uci del network.wwan.defaultroute && uci commit network && /etc/init.d/mwan3 enable && /etc/init.d/mwan3 start',
        'loadbalance_disable': "uci set network.wwan.defaultroute='0' && uci commit network && /etc/init.d/network restart && /etc/init.d/mwan3 disable && /etc/init.d/mwan3 stop",
        'reboot': 'reboot',
        'shutdown': 'poweroff'
    }

    if command not in valid_commands:
        return jsonify({"error": "Invalid command"}), 400

    try:
        # Execute each command in sequence
        cmd = valid_commands[command]
        for single_cmd in cmd.split('&&'):
            subprocess.run(single_cmd.strip(), shell=True, check=True)
        return jsonify({"status": "success"})
    except subprocess.CalledProcessError as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    speed_thread = Thread(target=speed_monitor, daemon=True)
    speed_thread.start()
    app.run(host="192.168.100.1",debug=True)