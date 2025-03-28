from flask import Flask, jsonify, request
import requests
import threading
import sqlite3
import time
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
auth = HTTPBasicAuth()
peers = []
port = 5000

# Database setup
def get_db_connection():
    conn = sqlite3.connect('kvstore.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# Create table if it doesn't exist
with get_db_connection() as conn:
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS store
                 (key TEXT PRIMARY KEY, value TEXT, timestamp REAL)''')
    conn.commit()

users = {
    "admin": generate_password_hash("secret")
}

@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username

# Helper functions
def get_peers():
    return [p for p in peers if p != f"http://127.0.0.1:{port}"]

def check_peer(peer):
    try:
        return requests.get(f"{peer}/ping", timeout=2).status_code == 200
    except requests.exceptions.RequestException:
        return False

def health_check():
    while True:
        for peer in get_peers().copy():
            if not check_peer(peer):
                print(f"Removing dead peer: {peer}")
                peers.remove(peer)
            else:
                print(f"Peer {peer} is alive")
        time.sleep(5)

# Endpoints
@app.route('/get/<key>', methods=['GET'])
def get(key):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT value, timestamp FROM store WHERE key=?", (key,))
        result = c.fetchone()
    return jsonify({key: result['value'] if result else None})

@app.route('/set/<key>/<value>', methods=['POST'])
@auth.login_required
def set_key(key, value):
    timestamp = time.time()
    
    # Update local storage
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO store 
                     VALUES (?, ?, ?)''', (key, value, timestamp))
        conn.commit()
    
    # Replicate to peers
    def replicate():
        for peer in get_peers():
            try:
                print(f"Attempting to replicate to {peer} with key: {key}, value: {value}, timestamp: {timestamp}")
                response = requests.post(
                    f"{peer}/replicate/{key}/{value}/{timestamp}",
                    timeout=2,
                    auth=(auth.username, "secret")
                )
                if response.status_code == 200:
                    print(f"Replication successful to {peer}: {response.json()}")
                else:
                    print(f"Replication failed to {peer}: {response.status_code} - {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"Replication failed to {peer} due to error: {e}")
    
    # Start the replication in a separate thread
    threading.Thread(target=replicate).start()
    return jsonify({"status": "success", "timestamp": timestamp})

@app.route('/replicate/<key>/<value>/<timestamp>', methods=['POST'])
@auth.login_required
def replicate(key, value, timestamp):
    timestamp = float(timestamp)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT timestamp FROM store WHERE key=?", (key,))
        result = c.fetchone()
        
        if not result or result['timestamp'] < timestamp:
            c.execute('''INSERT OR REPLACE INTO store 
                         VALUES (?, ?, ?)''', (key, value, timestamp))
            conn.commit()
    
    return jsonify({"status": "replicated"})

@app.route('/add_peer/<int:peer_port>', methods=['POST'])
def add_peer(peer_port):
    peer_url = f"http://127.0.0.1:{peer_port}"
    if peer_url not in peers:
        peers.append(peer_url)
        print(f"Added peer: {peer_url}")  # Debugging output
        return jsonify({"peers": peers}), 200
    print(f"Peer already exists: {peer_url}")  # Debugging output
    return jsonify({"error": "Peer already exists"}), 400

@app.route('/ping')
def ping():
    return "pong"

@app.route('/status')
def status():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM store")
        key_count = c.fetchone()[0]
    return jsonify({
        "node": f"http://127.0.0.1:{port}",
        "peers": get_peers(),
        "keys_stored": key_count,
        "status": "healthy"
    })

@app.route('/peers', methods=['GET'])
def get_peers_list():
    return jsonify({"peers": peers}), 200

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1])
    threading.Thread(target=health_check, daemon=True).start()
    app.run(host='0.0.0.0', port=port)