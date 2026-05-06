from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
import json, time, secrets, string, datetime, requests, threading, re
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

ADMIN_PASS = "admin"
DB_FILE = "licenses.json"
BLACKLIST_FILE = "blacklist.json"
CUSTOMERS_FILE = "customers.json"
DISCORD_WEBHOOK = ""  # Set your Discord webhook URL here

# ============ Database Functions ============

def load_db():
    try:
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            if "products" not in data:
                data["products"] = {}
            if "licenses" not in data:
                data["licenses"] = {}
            return data
    except:
        return {"products": {}, "licenses": {}}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_blacklist():
    try:
        with open(BLACKLIST_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_blacklist(data):
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_customers():
    try:
        with open(CUSTOMERS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_customers(data):
    with open(CUSTOMERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ============ Utility Functions ============

def generate_license(length=16):
    chars = string.ascii_uppercase + string.digits
    return "-".join(
        "".join(secrets.choice(chars) for _ in range(4))
        for _ in range(length // 4)
    )

def send_discord_notification(action, data):
    if not DISCORD_WEBHOOK:
        return
    
    colors = {
        "license_created": 0x00ff00,
        "license_deleted": 0xff0000,
        "license_expired": 0xff9900,
        "license_checked": 0x0099ff,
        "customer_added": 0x9b59b6,
        "product_added": 0x3498db,
        "ip_blocked": 0xe74c3c,
        "ip_unblocked": 0x2ecc71
    }
    
    embed = {
        "title": f"License System - {action.replace('_', ' ').title()}",
        "color": colors.get(action, 0xffffff),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "fields": []
    }
    
    for key, value in data.items():
        embed["fields"].append({
            "name": key.replace('_', ' ').title(),
            "value": str(value)[:1024],
            "inline": True
        })
    
    payload = {"embeds": [embed]}
    
    def send_async():
        try:
            requests.post(DISCORD_WEBHOOK, json=payload, timeout=5)
        except:
            pass
    
    threading.Thread(target=send_async).start()

def check_blacklist():
    ip = request.remote_addr
    blacklist = load_blacklist()
    
    for item in blacklist:
        if isinstance(item, dict):
            blocked_ip = item.get("ip", "")
        else:
            blocked_ip = item
        
        if "*" in blocked_ip:
            pattern = blocked_ip.replace(".", "\.").replace("*", ".*")
            if re.match(pattern, ip):
                send_discord_notification("ip_blocked", {
                    "ip": ip,
                    "blocked_pattern": blocked_ip,
                    "action": "Blocked (wildcard match)"
                })
                return jsonify({"error": "IP blocked", "valid": False}), 403
        elif blocked_ip == ip:
            send_discord_notification("ip_blocked", {
                "ip": ip,
                "action": "Blocked (exact match)"
            })
            return jsonify({"error": "IP blocked", "valid": False}), 403
    
    return None

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# ============ Template Filters ============

@app.template_filter("datetime")
def format_datetime(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

@app.template_filter("is_active")
def is_active(expire):
    if expire == 0:
        return True
    return time.time() < expire

# ============ API Endpoints ============

@app.route("/api/check", methods=["GET", "POST"])
def api_check_license():
    blocked = check_blacklist()
    if blocked:
        return blocked
    
    if request.method == "POST":
        data = request.json
        key = data.get("license")
        customer_name = data.get("customer")
    else:  # GET
        key = request.args.get("license")
        customer_name = request.args.get("customer")
    
    if not key:
        return jsonify({"error": "License key required", "valid": False}), 400
    
    db = load_db()
    customers = load_customers()
    
    if key in db["licenses"]:
        lic = db["licenses"][key]
        exp = lic["expire"]
        
        if lic["customer_id"] in customers:
            customers[lic["customer_id"]]["last_check"] = time.time()
            customers[lic["customer_id"]]["total_checks"] = customers[lic["customer_id"]].get("total_checks", 0) + 1
            save_customers(customers)
        
        if exp == 0 or time.time() < exp:
            product_info = db["products"].get(lic["product"], {})
            
            send_discord_notification("license_checked", {
                "license": key[:8] + "...",
                "customer": lic["customer"],
                "product": lic["product"],
                "status": "Valid",
                "ip": request.remote_addr
            })
            
            return jsonify({
                "valid": True,
                "customer": lic["customer"],
                "customer_id": lic.get("customer_id"),
                "product": lic["product"],
                "product_name": product_info.get("name", "Unknown"),
                "expire": exp,
                "expire_date": datetime.fromtimestamp(exp).isoformat() if exp > 0 else "Lifetime"
            })
    
    send_discord_notification("license_checked", {
        "license": key[:8] + "...",
        "status": "Invalid",
        "ip": request.remote_addr
    })
    
    return jsonify({"valid": False}), 404

@app.route("/api/info", methods=["GET"])
def api_license_info():
    blocked = check_blacklist()
    if blocked:
        return blocked
    
    key = request.args.get("license")
    if not key:
        return jsonify({"error": "License key required"}), 400
    
    db = load_db()
    if key in db["licenses"]:
        lic = db["licenses"][key]
        product_info = db["products"].get(lic["product"], {})
        return jsonify({
            "license": key,
            "customer": lic["customer"],
            "product": lic["product"],
            "product_name": product_info.get("name", "Unknown"),
            "created": lic["created"],
            "expire": lic["expire"],
            "expire_date": datetime.fromtimestamp(lic["expire"]).isoformat() if lic["expire"] > 0 else "Lifetime"
        })
    
    return jsonify({"error": "License not found"}), 404

@app.route("/api/customer/licenses", methods=["GET"])
def api_customer_licenses():
    customer_id = request.args.get("customer_id")
    if not customer_id:
        return jsonify({"error": "Customer ID required"}), 400
    
    db = load_db()
    customers = load_customers()
    
    if customer_id not in customers:
        return jsonify({"error": "Customer not found"}), 404
    
    customer_licenses = []
    for key, lic in db["licenses"].items():
        if lic.get("customer_id") == customer_id:
            product_info = db["products"].get(lic["product"], {})
            customer_licenses.append({
                "license": key,
                "product": lic["product"],
                "product_name": product_info.get("name", "Unknown"),
                "created": lic["created"],
                "expire": lic["expire"],
                "status": "active" if (lic["expire"] == 0 or time.time() < lic["expire"]) else "expired"
            })
    
    return jsonify({
        "customer": customers[customer_id]["name"],
        "licenses": customer_licenses,
        "total": len(customer_licenses)
    })

# ============ Web Interface ============

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>License Manager - Login</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
        }
        
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        .login-container {
            width: 100%;
            max-width: 400px;
            animation: fadeIn 0.5s ease;
        }
        
        .login-card {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 12px;
            padding: 40px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.3);
            backdrop-filter: blur(10px);
        }
        
        .logo {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .logo h1 {
            color: #333;
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 8px;
        }
        
        .logo p {
            color: #666;
            font-size: 14px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 500;
            font-size: 14px;
        }
        
        .form-input {
            width: 100%;
            padding: 12px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s;
            background: white;
        }
        
        .form-input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .btn-login {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .btn-login:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.3);
        }
        
        .alert {
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .alert-error {
            background: #fee;
            color: #c33;
            border: 1px solid #fcc;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .copyright {
            text-align: center;
            margin-top: 20px;
            color: rgba(255, 255, 255, 0.8);
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-card">
            <div class="logo">
                <h1>🔐 License Manager</h1>
                <p>Administrator Login</p>
            </div>
            
            {% if error %}
            <div class="alert alert-error">
                <span>⚠️</span>
                <span>Invalid credentials. Please try again.</span>
            </div>
            {% endif %}
            
            <form method="post">
                <div class="form-group">
                    <label class="form-label">Password</label>
                    <input type="password" name="password" class="form-input" placeholder="Enter admin password" required autofocus>
                </div>
                <button type="submit" class="btn-login">Login to Dashboard</button>
            </form>
        </div>
        <div class="copyright">
            © 2024 License Manager v2.0
        </div>
    </div>
</body>
</html>
"""

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["password"] == ADMIN_PASS:
            session["logged_in"] = True
            session["login_time"] = time.time()
            return redirect(url_for("dashboard"))
        return render_template_string(LOGIN_HTML, error=True)
    return render_template_string(LOGIN_HTML, error=False)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    db = load_db()
    customers = load_customers()
    current_time = time.time()
    
    total_licenses = len(db["licenses"])
    active_licenses = len([l for l in db["licenses"].values() 
                          if l["expire"] == 0 or current_time < l["expire"]])
    lifetime_licenses = len([l for l in db["licenses"].values() if l["expire"] == 0])
    expiring_soon = len([l for l in db["licenses"].values() 
                        if l["expire"] > 0 and l["expire"] < current_time + 86400*7])
    
    return render_template_string(DASHBOARD_HTML, 
                                 licenses=db["licenses"], 
                                 products=db["products"],
                                 customers=customers,
                                 current_time=current_time,
                                 stats={
                                     "total": total_licenses,
                                     "active": active_licenses,
                                     "lifetime": lifetime_licenses,
                                     "expiring_soon": expiring_soon,
                                     "total_customers": len(customers)
                                 })

@app.route("/customers")
@login_required
def customers_page():
    customers = load_customers()
    db = load_db()
    
    for customer_id, customer in customers.items():
        license_count = len([l for l in db["licenses"].values() 
                           if l.get("customer_id") == customer_id])
        customer["license_count"] = license_count
    
    return render_template_string(CUSTOMERS_HTML, customers=customers)

@app.route("/products")
@login_required
def products():
    db = load_db()
    return render_template_string(PRODUCTS_HTML, products=db["products"])

@app.route("/blacklist")
@login_required
def blacklist_page():
    blacklist = load_blacklist()
    return render_template_string(BLACKLIST_HTML, blacklist=blacklist)

# ============ Customer Management ============

@app.route("/customer/add", methods=["POST"])
@login_required
def add_customer():
    customer_id = request.form.get("customer_id")
    customer_name = request.form.get("customer_name")
    customer_email = request.form.get("customer_email")
    discord_id = request.form.get("discord_id")
    
    if customer_id and customer_name:
        customers = load_customers()
        customers[customer_id] = {
            "name": customer_name,
            "email": customer_email,
            "discord_id": discord_id,
            "created": time.time(),
            "notes": request.form.get("notes", "")
        }
        save_customers(customers)
        
        send_discord_notification("customer_added", {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "email": customer_email,
            "discord_id": discord_id
        })
    
    return redirect(url_for("customers_page"))

@app.route("/customer/edit/<customer_id>", methods=["POST"])
@login_required
def edit_customer(customer_id):
    customers = load_customers()
    if customer_id in customers:
        customers[customer_id]["name"] = request.form.get("customer_name", customers[customer_id]["name"])
        customers[customer_id]["email"] = request.form.get("customer_email", customers[customer_id].get("email", ""))
        customers[customer_id]["discord_id"] = request.form.get("discord_id", customers[customer_id].get("discord_id", ""))
        customers[customer_id]["notes"] = request.form.get("notes", customers[customer_id].get("notes", ""))
        save_customers(customers)
    
    return redirect(url_for("customers_page"))

@app.route("/customer/remove/<customer_id>")
@login_required
def remove_customer(customer_id):
    customers = load_customers()
    if customer_id in customers:
        del customers[customer_id]
        save_customers(customers)
    
    return redirect(url_for("customers_page"))

# ============ Product Management ============

@app.route("/product/add", methods=["POST"])
@login_required
def add_product():
    product_id = request.form.get("product_id")
    product_name = request.form.get("product_name")
    price = request.form.get("price", "")
    
    if product_id and product_name:
        db = load_db()
        db["products"][product_id] = {
            "name": product_name,
            "price": price,
            "created": time.time(),
            "notes": request.form.get("notes", "")
        }
        save_db(db)
        
        send_discord_notification("product_added", {
            "product_id": product_id,
            "product_name": product_name,
            "price": price
        })
    
    return redirect(url_for("products"))

@app.route("/product/remove/<product_id>")
@login_required
def remove_product(product_id):
    db = load_db()
    if product_id in db["products"]:
        del db["products"][product_id]
        save_db(db)
    return redirect(url_for("products"))

# ============ License Management ============

@app.route("/license/add", methods=["POST"])
@login_required
def add_license():
    days = int(request.form.get("days", 0))
    customer_id = request.form.get("customer_id")
    product = request.form.get("product", "default")
    notes = request.form.get("notes", "")
    
    customers = load_customers()
    customer_name = customers.get(customer_id, {}).get("name", "Unknown") if customer_id else "Unknown"
    
    key = generate_license()
    db = load_db()
    
    expire = 0 if days == 0 else time.time() + days * 86400
    db["licenses"][key] = {
        "expire": expire,
        "customer": customer_name,
        "customer_id": customer_id,
        "product": product,
        "created": time.time(),
        "notes": notes
    }
    save_db(db)
    
    send_discord_notification("license_created", {
        "license": key,
        "customer": customer_name,
        "customer_id": customer_id,
        "product": product,
        "days": days if days > 0 else "Lifetime",
        "expires": datetime.fromtimestamp(expire).strftime("%Y-%m-%d") if expire > 0 else "Never"
    })
    
    return redirect(url_for("dashboard"))

@app.route("/license/bulk", methods=["POST"])
@login_required
def bulk_generate():
    count = int(request.form.get("count", 1))
    days = int(request.form.get("days", 0))
    product = request.form.get("product", "default")
    customer_id = request.form.get("customer_id")
    
    customers = load_customers()
    customer_name = customers.get(customer_id, {}).get("name", "Unknown") if customer_id else "Unknown"
    
    db = load_db()
    generated_keys = []
    
    for _ in range(count):
        key = generate_license()
        expire = 0 if days == 0 else time.time() + days * 86400
        
        db["licenses"][key] = {
            "expire": expire,
            "customer": customer_name,
            "customer_id": customer_id,
            "product": product,
            "created": time.time(),
            "notes": f"Bulk generated - {count} keys"
        }
        generated_keys.append(key)
    
    save_db(db)
    
    send_discord_notification("license_created", {
        "action": "Bulk Generate",
        "count": count,
        "customer": customer_name,
        "product": product,
        "sample_key": generated_keys[0] if generated_keys else "None"
    })
    
    return render_template_string(BULK_RESULT_HTML, keys=generated_keys)

@app.route("/license/remove/<license_key>")
@login_required
def remove_license(license_key):
    db = load_db()
    if license_key in db["licenses"]:
        license_data = db["licenses"][license_key]
        del db["licenses"][license_key]
        save_db(db)
        
        send_discord_notification("license_deleted", {
            "license": license_key,
            "customer": license_data.get("customer", "Unknown"),
            "product": license_data.get("product", "Unknown")
        })
    
    return redirect(url_for("dashboard"))

# ============ Blacklist Management ============

@app.route("/blacklist/add", methods=["POST"])
@login_required
def add_to_blacklist():
    ip = request.form.get("ip")
    reason = request.form.get("reason", "")
    
    if ip:
        blacklist = load_blacklist()
        ip_exists = False
        
        for item in blacklist:
            if isinstance(item, dict):
                if item.get("ip") == ip:
                    ip_exists = True
                    break
            else:
                if item == ip:
                    ip_exists = True
                    break
        
        if not ip_exists:
            blacklist.append({
                "ip": ip,
                "reason": reason,
                "added": time.time(),
                "added_by": "Admin"
            })
            save_blacklist(blacklist)
            
            send_discord_notification("ip_blocked", {
                "ip": ip,
                "reason": reason,
                "action": "Added to blacklist"
            })
    
    return redirect(url_for("blacklist_page"))

@app.route("/blacklist/remove/<ip>")
@login_required
def remove_from_blacklist(ip):
    blacklist = load_blacklist()
    new_blacklist = []
    
    for item in blacklist:
        if isinstance(item, dict):
            if item.get("ip") != ip:
                new_blacklist.append(item)
        else:
            if item != ip:
                new_blacklist.append(item)
    
    save_blacklist(new_blacklist)
    
    send_discord_notification("ip_unblocked", {
        "ip": ip,
        "action": "Removed from blacklist"
    })
    
    return redirect(url_for("blacklist_page"))

# ============ Webhook Test ============

@app.route("/test/webhook", methods=["POST"])
@login_required
def test_webhook():
    if DISCORD_WEBHOOK:
        send_discord_notification("test", {
            "message": "Webhook test successful",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "system": "License Manager"
        })
        return jsonify({"status": "Test notification sent"})
    return jsonify({"error": "No webhook configured"}), 400

# ============ HTML Templates ============

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - License Manager</title>
    <style>

        * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
            "Segoe UI", Roboto, sans-serif;
        }

        :root{
        --bg0:#05060a;
        --bg1:#070a12;

        --text: rgba(255,255,255,.92);
        --text2: rgba(255,255,255,.68);
        --text3: rgba(255,255,255,.48);

        /* liquid glass */
        --glass: rgba(255,255,255,.06);
        --glass2: rgba(255,255,255,.10);
        --stroke: rgba(255,255,255,.12);
        --stroke2: rgba(255,255,255,.18);

        --blur: 22px;
        --blurStrong: 34px;

        --radius: 22px;
        --radius2: 18px;
        --pill: 999px;

        /* iOS accent */
        --accentA: #6ea8ff;
        --accentB: #9b7bff;
        --success: #2bd576;
        --danger: #ff4d57;
        --warning:#ffcc47;

        --shadow: 0 25px 70px rgba(0,0,0,.65);
        --shadowSoft: 0 12px 30px rgba(0,0,0,.45);

        color-scheme: dark;
        }

        body{
        background:
            radial-gradient(1200px 800px at 20% 10%, rgba(110,168,255,.18), transparent 55%),
            radial-gradient(1000px 700px at 85% 20%, rgba(155,123,255,.14), transparent 55%),
            radial-gradient(900px 650px at 45% 95%, rgba(43,213,118,.10), transparent 60%),
            linear-gradient(180deg, var(--bg0) 0%, var(--bg1) 100%);
        color: var(--text);
        line-height: 1.5;
        }

        /* subtle animated noise for liquid look */
        body::before{
        content:"";
        position: fixed;
        inset:0;
        pointer-events:none;
        opacity:.10;
        mix-blend-mode: overlay;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='260'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='260' height='260' filter='url(%23n)' opacity='.55'/%3E%3C/svg%3E");
        }

        /* ===== Liquid surface helper ===== */
        .liquid{
        position: relative;
        overflow: hidden;
        border: 1px solid var(--stroke);
        background: linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.05));
        backdrop-filter: blur(var(--blurStrong)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blurStrong)) saturate(170%);
        box-shadow: var(--shadowSoft);
        }

        /* specular highlight + edge glow */
        .liquid::before{
        content:"";
        position:absolute;
        inset:-40% -20%;
        background:
            radial-gradient(600px 240px at 30% 10%, rgba(255,255,255,.22), transparent 60%),
            radial-gradient(520px 260px at 80% 0%, rgba(255,255,255,.12), transparent 55%),
            radial-gradient(520px 320px at 50% 120%, rgba(110,168,255,.10), transparent 55%);
        opacity:.55;
        pointer-events:none;
        transform: rotate(-8deg);
        }

        /* glossy stroke */
        .liquid::after{
        content:"";
        position:absolute;
        inset:0;
        border-radius: inherit;
        pointer-events:none;
        box-shadow:
            inset 0 1px 0 rgba(255,255,255,.18),
            inset 0 -1px 0 rgba(0,0,0,.35);
        opacity:.9;
        }

        /* ===== Top Bar ===== */
        .top-bar{
        height: 66px;
        position: fixed;
        top:0; left:0; right:0;
        z-index:1000;
        padding: 0 18px;
        display:flex;
        align-items:center;
        justify-content:space-between;
        border-bottom: 1px solid rgba(255,255,255,.10);

        background: rgba(10,12,18,.55);
        backdrop-filter: blur(var(--blurStrong)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blurStrong)) saturate(170%);
        }

        .top-bar::before{
        content:"";
        position:absolute;
        inset:0;
        background:
            radial-gradient(700px 260px at 25% 0%, rgba(255,255,255,.12), transparent 60%),
            radial-gradient(700px 260px at 80% 0%, rgba(155,123,255,.10), transparent 60%);
        pointer-events:none;
        opacity:.6;
        }

        .logo{
        position:relative;
        display:flex;
        align-items:center;
        gap:10px;
        font-size: 18px;
        font-weight: 700;
        letter-spacing:.2px;
        }

        .user-menu{
        position:relative;
        display:flex;
        align-items:center;
        gap:10px;
        }

        /* ===== Side Menu (liquid dock) ===== */
        .admin-menu{
        position: fixed;
        top:66px;
        right: 14px;
        bottom: 14px;
        width: 250px;
        padding: 12px 10px;
        border-radius: var(--radius);
        overflow-y:auto;

        border: 1px solid rgba(255,255,255,.10);
        background: rgba(12,14,22,.52);
        backdrop-filter: blur(var(--blurStrong)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blurStrong)) saturate(170%);
        box-shadow: var(--shadow);
        }

        .admin-menu::-webkit-scrollbar{ width:8px; }
        .admin-menu::-webkit-scrollbar-thumb{
        background: rgba(255,255,255,.10);
        border-radius: 999px;
        }

        .menu-item{
        display:flex;
        align-items:center;
        gap:12px;
        padding: 12px 12px;
        margin: 7px 6px;
        border-radius: var(--pill);
        text-decoration:none;
        color: var(--text2);
        border: 1px solid transparent;
        position: relative;
        transition: transform .18s ease, background .2s ease, border-color .2s ease;
        background: transparent;
        }

        /* iOS hover lift */
        .menu-item:hover{
        background: rgba(255,255,255,.06);
        border-color: rgba(255,255,255,.10);
        color: var(--text);
        transform: translateY(-1px);
        }

        .menu-item.active{
        color: var(--text);
        border-color: rgba(110,168,255,.28);
        background: linear-gradient(180deg, rgba(110,168,255,.18), rgba(155,123,255,.10));
        box-shadow: 0 12px 30px rgba(110,168,255,.12);
        }

        .menu-item.active::after{
        content:"";
        position:absolute;
        left: 10px;
        top: 50%;
        width: 7px;
        height: 7px;
        transform: translateY(-50%);
        border-radius: 999px;
        background: linear-gradient(180deg, var(--accentA), var(--accentB));
        box-shadow: 0 0 18px rgba(110,168,255,.25);
        }

        /* ===== Main content ===== */
        .wrapper{
        margin-top: 66px;
        margin-right: 290px; /* because menu is inset now */
        padding: 22px;
        min-height: calc(100vh - 66px);
        }

        .page-title{
        font-size: 22px;
        font-weight: 750;
        margin: 10px 0 18px 0;
        }

        /* ===== Cards (Liquid) ===== */
        .card{
        border-radius: var(--radius);
        }
        .card, .stat-card, .modal-content{
        position: relative;
        overflow: hidden;
        border: 1px solid var(--stroke);
        background: rgba(255,255,255,.06);
        backdrop-filter: blur(var(--blurStrong)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blurStrong)) saturate(170%);
        box-shadow: var(--shadowSoft);
        }

        .card::before, .stat-card::before, .modal-content::before{
        content:"";
        position:absolute;
        inset:-40% -20%;
        background:
            radial-gradient(600px 240px at 30% 10%, rgba(255,255,255,.20), transparent 60%),
            radial-gradient(520px 260px at 80% 0%, rgba(255,255,255,.10), transparent 55%),
            radial-gradient(520px 320px at 50% 120%, rgba(110,168,255,.08), transparent 55%);
        opacity:.60;
        pointer-events:none;
        transform: rotate(-8deg);
        }

        .card::after, .stat-card::after, .modal-content::after{
        content:"";
        position:absolute;
        inset:0;
        pointer-events:none;
        border-radius: inherit;
        box-shadow:
            inset 0 1px 0 rgba(255,255,255,.18),
            inset 0 -1px 0 rgba(0,0,0,.40);
        }

        .card-header{
        padding: 18px;
        border-bottom: 1px solid rgba(255,255,255,.10);
        font-size: 15px;
        font-weight: 800;
        display:flex;
        align-items:center;
        gap:10px;
        }

        .card-body{ padding: 18px; }

        /* ===== Stats ===== */
        .stats-grid{
        display:grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 16px;
        margin-bottom: 26px;
        }

        .stat-card{
        border-radius: var(--radius);
        padding: 18px;
        }

        .stat-number{
        font-size: 30px;
        font-weight: 800;
        letter-spacing:.2px;
        background: linear-gradient(90deg, var(--accentA), var(--accentB));
        -webkit-background-clip:text;
        background-clip:text;
        color: transparent;
        margin-bottom: 6px;
        }

        .stat-label{ color: var(--text3); font-size: 13px; }

        /* ===== iOS Liquid Buttons ===== */
        .btn{
        position: relative;
        padding: 10px 14px;
        border-radius: var(--pill);
        border: 1px solid rgba(255,255,255,.14);
        background: rgba(255,255,255,.07);
        color: var(--text);
        font-size: 13px;
        font-weight: 750;
        cursor: pointer;
        display:inline-flex;
        align-items:center;
        gap:8px;
        text-decoration:none;

        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        box-shadow:
            0 10px 26px rgba(0,0,0,.45),
            inset 0 1px 0 rgba(255,255,255,.18);
        transition: transform .14s ease, box-shadow .18s ease, border-color .18s ease, background .18s ease;
        overflow:hidden;
        }

        /* glossy sweep */
        .btn::before{
        content:"";
        position:absolute;
        inset:-60% -30%;
        background:
            radial-gradient(260px 120px at 30% 20%, rgba(255,255,255,.26), transparent 60%),
            radial-gradient(240px 120px at 75% 10%, rgba(255,255,255,.12), transparent 60%);
        opacity:.55;
        transform: rotate(-12deg);
        pointer-events:none;
        }

        .btn:hover{
        transform: translateY(-1px) scale(1.01);
        background: rgba(255,255,255,.09);
        border-color: rgba(255,255,255,.22);
        box-shadow:
            0 16px 36px rgba(0,0,0,.55),
            inset 0 1px 0 rgba(255,255,255,.20);
        }

        .btn:active{
        transform: translateY(0px) scale(.985);
        box-shadow:
            0 10px 24px rgba(0,0,0,.50),
            inset 0 2px 10px rgba(0,0,0,.35);
        }

        .btn-primary{
        border-color: rgba(110,168,255,.30);
        background: linear-gradient(180deg, rgba(110,168,255,.22), rgba(155,123,255,.14));
        }

        .btn-danger{
        border-color: rgba(255,77,87,.35);
        background: linear-gradient(180deg, rgba(255,77,87,.22), rgba(255,77,87,.12));
        }

        .btn-success{
        border-color: rgba(43,213,118,.35);
        background: linear-gradient(180deg, rgba(43,213,118,.18), rgba(43,213,118,.10));
        }

        .btn-sm{
        padding: 8px 12px;
        font-size: 12px;
        }

        /* ===== Forms ===== */
        .form-row{ display:flex; gap:14px; margin-bottom: 16px; }
        .form-group{ flex:1; }

        .form-label{
        display:block;
        margin-bottom: 8px;
        font-weight: 800;
        color: var(--text2);
        font-size: 12.5px;
        }

        .form-control{
        width:100%;
        padding: 11px 12px;
        border-radius: 16px;
        border: 1px solid rgba(255,255,255,.12);
        background: rgba(255,255,255,.06);
        color: var(--text);
        font-size: 13px;

        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        box-shadow: inset 0 1px 0 rgba(255,255,255,.12);
        transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
        }

        .form-control::placeholder{ color: rgba(255,255,255,.30); }

        .form-control:focus{
        outline:none;
        background: rgba(255,255,255,.08);
        border-color: rgba(110,168,255,.45);
        box-shadow: 0 0 0 3px rgba(110,168,255,.16);
        }

        /* ===== Table ===== */
        .table-container{
        overflow-x:auto;
        border-radius: var(--radius);
        }

        .wp-list-table{
        width:100%;
        border-collapse: collapse;
        background: transparent;
        }

        .wp-list-table thead th{
        padding: 12px;
        text-align:left;
        font-weight: 800;
        color: var(--text3);
        border-bottom: 1px solid rgba(255,255,255,.10);
        background: rgba(255,255,255,.04);
        position: sticky;
        top: 0;
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        }

        .wp-list-table tbody tr{
        border-bottom: 1px solid rgba(255,255,255,.08);
        }

        .wp-list-table tbody tr:hover{
        background: rgba(255,255,255,.05);
        }

        .wp-list-table td{
        padding: 12px;
        color: var(--text2);
        }

        /* ===== Badges (pill iOS) ===== */
        .badge{
        display:inline-flex;
        align-items:center;
        padding: 6px 10px;
        border-radius: var(--pill);
        font-size: 12px;
        font-weight: 850;
        border: 1px solid rgba(255,255,255,.12);
        background: rgba(255,255,255,.06);
        }

        .badge-success{ border-color: rgba(43,213,118,.28); background: rgba(43,213,118,.12); }
        .badge-danger{ border-color: rgba(255,77,87,.28); background: rgba(255,77,87,.12); }
        .badge-warning{ border-color: rgba(255,204,71,.28); background: rgba(255,204,71,.12); }
        .badge-info{ border-color: rgba(110,168,255,.28); background: rgba(110,168,255,.12); }

        /* ===== License key ===== */
        .license-key{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        padding: 8px 12px;
        border-radius: 16px;
        border: 1px solid rgba(255,255,255,.12);
        background: rgba(255,255,255,.06);
        color: var(--text);
        }

        /* ===== Modal ===== */
        .modal{
        display:none;
        position: fixed;
        inset:0;
        background: rgba(0,0,0,.55);
        z-index:2000;
        align-items:center;
        justify-content:center;
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        }

        .modal-content{
        width: 92%;
        max-width: 540px;
        max-height: 90vh;
        overflow-y:auto;
        border-radius: 26px;
        }

        .modal-header{
        padding: 18px;
        border-bottom: 1px solid rgba(255,255,255,.10);
        display:flex;
        justify-content: space-between;
        align-items:center;
        }
        .modal-body{ padding: 18px; }
        .modal-footer{
        padding: 18px;
        border-top: 1px solid rgba(255,255,255,.10);
        display:flex;
        justify-content:flex-end;
        gap:10px;
        }

        /* ===== Responsive ===== */
        @media (max-width: 768px){
        .admin-menu{ width: 86px; right: 10px; }
        .admin-menu .menu-text{ display:none; }
        .wrapper{ margin-right: 110px; }
        .form-row{ flex-direction: column; }
        .stats-grid{ grid-template-columns: 1fr; }
        }

        .no-results{
        text-align:center;
        padding: 40px 20px;
        color: var(--text3);
        }
        .no-results i{
        font-size: 46px;
        margin-bottom: 14px;
        opacity: .55;
        }

        /* focus ring iOS */
        a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible {
        outline:none;
        box-shadow: 0 0 0 3px rgba(110,168,255,.18);
        border-color: rgba(110,168,255,.45);
        }


    </style>
</head>
<body>
    <!-- Top Bar -->
    <div class="top-bar">
        <div class="logo">
            <span>📊</span>
            <span>License Manager</span>
        </div>
        <div class="user-menu">
            <a href="{{ url_for('dashboard') }}" class="btn btn-primary">
                <span>🏠</span>
                <span>Dashboard</span>
            </a>
            <a href="{{ url_for('logout') }}" class="btn btn-danger">
                <span>🚪</span>
                <span>Logout</span>
            </a>
        </div>
    </div>
    
    <!-- Side Menu -->
    <nav class="admin-menu">
        <a href="{{ url_for('dashboard') }}" class="menu-item active">
            <span>📊</span>
            <span class="menu-text">Dashboard</span>
        </a>
        <a href="{{ url_for('customers_page') }}" class="menu-item">
            <span>👥</span>
            <span class="menu-text">Customers</span>
        </a>
        <a href="{{ url_for('products') }}" class="menu-item">
            <span>📦</span>
            <span class="menu-text">Products</span>
        </a>
        <a href="{{ url_for('blacklist_page') }}" class="menu-item">
            <span>🛑</span>
            <span class="menu-text">Blacklist</span>
        </a>
    </nav>
    
    <!-- Main Content -->
    <div class="wrapper">
        <h1 class="page-title">Dashboard</h1>
        
        <!-- Stats -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon">🔑</div>
                <div class="stat-number">{{ stats.total }}</div>
                <div class="stat-label">Total Licenses</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">✅</div>
                <div class="stat-number">{{ stats.active }}</div>
                <div class="stat-label">Active Licenses</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">♾️</div>
                <div class="stat-number">{{ stats.lifetime }}</div>
                <div class="stat-label">Lifetime Licenses</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">👥</div>
                <div class="stat-number">{{ stats.total_customers }}</div>
                <div class="stat-label">Customers</div>
            </div>
        </div>
        
        <!-- Generate License -->
        <div class="card">
            <div class="card-header">
                <span>➕</span>
                <span>Generate New License</span>
            </div>
            <div class="card-body">
                <form method="post" action="{{ url_for('add_license') }}">
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Customer</label>
                            <select name="customer_id" class="form-control" required>
                                <option value="">Select Customer</option>
                                {% for customer_id, customer in customers.items() %}
                                <option value="{{ customer_id }}">{{ customer.name }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">Product</label>
                            <select name="product" class="form-control" required>
                                {% for pid, pdata in products.items() %}
                                <option value="{{ pid }}">{{ pdata.name }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">Duration (Days)</label>
                            <input type="number" name="days" class="form-control" placeholder="0 = Lifetime" min="0">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Notes</label>
                            <input type="text" name="notes" class="form-control" placeholder="Optional notes">
                        </div>
                        <div class="form-group" style="align-self: flex-end;">
                            <button type="submit" class="btn btn-success">
                                <span>✅</span>
                                <span>Generate License</span>
                            </button>
                            <button type="button" class="btn btn-primary" onclick="openBulkModal()">
                                <span>📦</span>
                                <span>Bulk Generate</span>
                            </button>
                        </div>
                    </div>
                </form>
            </div>
        </div>
        
        <!-- Licenses Table -->
        <div class="card">
            <div class="card-header">
                <span>📋</span>
                <span>License Management</span>
                <span class="badge" style="margin-left: 10px;">{{ licenses|length }} licenses</span>
            </div>
            <div class="card-body">
                {% if licenses %}
                <div class="table-container">
                    <table class="wp-list-table">
                        <thead>
                            <tr>
                                <th>License Key</th>
                                <th>Customer</th>
                                <th>Product</th>
                                <th>Created</th>
                                <th>Expires</th>
                                <th>Status</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for key, data in licenses.items() %}
                            <tr>
                                <td>
                                    <code class="license-key">{{ key }}</code>
                                </td>
                                <td>{{ data.customer }}</td>
                                <td>
                                    {% if data.product in products %}
                                    {{ products[data.product].name }}
                                    {% else %}
                                    {{ data.product }}
                                    {% endif %}
                                </td>
                                <td>{{ (data.created | int) | datetime }}</td>
                                <td>
                                    {% if data.expire == 0 %}
                                    <span class="badge badge-info">Lifetime</span>
                                    {% else %}
                                    {{ (data.expire | int) | datetime }}
                                    {% endif %}
                                </td>
                                <td>
                                    {% if data.expire == 0 or current_time < data.expire %}
                                    <span class="badge badge-success">Active</span>
                                    {% else %}
                                    <span class="badge badge-danger">Expired</span>
                                    {% endif %}
                                </td>
                                <td>
                                    <div class="action-buttons">
                                        <a href="{{ url_for('remove_license', license_key=key) }}" 
                                           class="btn btn-danger btn-sm"
                                           onclick="return confirm('Are you sure you want to delete this license?')">
                                            <span>🗑️</span>
                                            <span>Delete</span>
                                        </a>
                                    </div>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="no-results">
                    <span>📭</span>
                    <h3>No Licenses Found</h3>
                    <p>Generate your first license using the form above.</p>
                </div>
                {% endif %}
            </div>
        </div>
    </div>
    
    <!-- Bulk Generate Modal -->
    <div class="modal" id="bulkModal">
        <div class="modal-content">
            <div class="modal-header">
                <h2>Bulk Generate Licenses</h2>
                <button type="button" onclick="closeBulkModal()" style="background: none; border: none; font-size: 20px; cursor: pointer;">×</button>
            </div>
            <form method="post" action="{{ url_for('bulk_generate') }}">
                <div class="modal-body">
                    <div class="form-group">
                        <label class="form-label">Number of Licenses</label>
                        <input type="number" name="count" class="form-control" value="5" min="1" max="100" required>
                    </div>
                    <div class="form-group">
                        <label class="form-label">Customer</label>
                        <select name="customer_id" class="form-control">
                            <option value="">No Customer</option>
                            {% for customer_id, customer in customers.items() %}
                            <option value="{{ customer_id }}">{{ customer.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label">Product</label>
                        <select name="product" class="form-control" required>
                            {% for pid, pdata in products.items() %}
                            <option value="{{ pid }}">{{ pdata.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label">Duration (Days)</label>
                        <input type="number" name="days" class="form-control" value="30" min="0">
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn" onclick="closeBulkModal()">Cancel</button>
                    <button type="submit" class="btn btn-success">Generate</button>
                </div>
            </form>
        </div>
    </div>
    
    <script>
        function openBulkModal() {
            document.getElementById('bulkModal').style.display = 'flex';
        }
        
        function closeBulkModal() {
            document.getElementById('bulkModal').style.display = 'none';
        }
        
        // Close modal when clicking outside
        document.getElementById('bulkModal').addEventListener('click', function(e) {
            if (e.target === this) {
                closeBulkModal();
            }
        });
    </script>
</body>
</html>
"""

CUSTOMERS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Customers - License Manager</title>
    <style>
    *{
    margin:0;
    padding:0;
    box-sizing:border-box;
    font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Segoe UI",Roboto,Oxygen,Ubuntu,sans-serif;
    }

        :root{
        --bg0:#05060a;
        --bg1:#070a12;

        --text:rgba(255,255,255,.92);
        --text-light:rgba(255,255,255,.62);

        --glass:rgba(255,255,255,.06);
        --glass2:rgba(255,255,255,.10);

        --border:rgba(255,255,255,.12);
        --border2:rgba(255,255,255,.18);

        --primary:#6ea8ff;
        --primary-dark:#4e8cff;

        --success:#2bd576;
        --danger:#ff4d57;
        --warning:#ffcc47;

        --radius:22px;
        --radius2:18px;
        --pill:999px;

        --blur:22px;
        --blur-strong:34px;

        --shadow:0 24px 70px rgba(0,0,0,.65);
        --shadow-soft:0 12px 28px rgba(0,0,0,.45);

        color-scheme:dark;
        }

        body{
        background:
            radial-gradient(1200px 800px at 20% 10%, rgba(110,168,255,.20), transparent 55%),
            radial-gradient(1000px 700px at 85% 20%, rgba(155,123,255,.16), transparent 55%),
            radial-gradient(900px 650px at 45% 95%, rgba(43,213,118,.10), transparent 60%),
            linear-gradient(180deg,var(--bg0) 0%,var(--bg1) 100%);
        color:var(--text);
        line-height:1.5;
        }

        /* subtle grain */
        body::before{
        content:"";
        position:fixed;
        inset:0;
        pointer-events:none;
        opacity:.10;
        mix-blend-mode:overlay;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='260'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='260' height='260' filter='url(%23n)' opacity='.55'/%3E%3C/svg%3E");
        }

        /* =========================================================
        LIQUID SURFACE (cards, menus, modals) - iOS vibe
        ========================================================= */
        .card,
        .admin-menu,
        .modal-content{
        backdrop-filter: blur(var(--blur-strong)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur-strong)) saturate(170%);
        }

        .card,
        .modal-content{
        position:relative;
        overflow:hidden;
        border:1px solid var(--border);
        border-radius:var(--radius);
        background:rgba(255,255,255,.055);
        box-shadow:var(--shadow-soft);
        }

        .card::before,
        .modal-content::before{
        content:"";
        position:absolute;
        inset:-45% -25%;
        background:
            radial-gradient(640px 260px at 30% 10%, rgba(255,255,255,.22), transparent 62%),
            radial-gradient(520px 260px at 80% 0%, rgba(255,255,255,.10), transparent 60%),
            radial-gradient(560px 380px at 50% 120%, rgba(110,168,255,.09), transparent 60%);
        opacity:.62;
        transform:rotate(-8deg);
        pointer-events:none;
        }

        .card::after,
        .modal-content::after{
        content:"";
        position:absolute;
        inset:0;
        border-radius:inherit;
        pointer-events:none;
        box-shadow:
            inset 0 1px 0 rgba(255,255,255,.18),
            inset 0 -1px 0 rgba(0,0,0,.42);
        }

        /* =========================================================
        TOP BAR
        ========================================================= */
        .top-bar{
        height:66px;
        padding:0 18px;
        display:flex;
        align-items:center;
        justify-content:space-between;
        position:fixed;
        top:0; left:0; right:0;
        z-index:1000;

        background:rgba(12,14,22,.55);
        border-bottom:1px solid rgba(255,255,255,.10);
        backdrop-filter: blur(var(--blur-strong)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur-strong)) saturate(170%);
        }

        .top-bar::before{
        content:"";
        position:absolute;
        inset:0;
        background:
            radial-gradient(700px 260px at 25% 0%, rgba(255,255,255,.12), transparent 60%),
            radial-gradient(700px 260px at 80% 0%, rgba(155,123,255,.10), transparent 60%);
        pointer-events:none;
        opacity:.65;
        }

        .logo{
        position:relative;
        display:flex;
        align-items:center;
        gap:10px;
        font-size:18px;
        font-weight:900;
        letter-spacing:.2px;
        }

        .user-menu{
        position:relative;
        display:flex;
        align-items:center;
        gap:10px;
        }

        /* =========================================================
        BUTTONS (REAL iPhone-like pills)
        ========================================================= */
        .btn{
        position:relative;
        padding:10px 14px;
        border-radius:var(--pill);
        border:1px solid rgba(255,255,255,.14);
        background:rgba(255,255,255,.07);
        color:var(--text);
        cursor:pointer;
        font-size:13px;
        font-weight:950;
        transition:transform .14s ease, box-shadow .18s ease, border-color .18s ease, background .18s ease;
        text-decoration:none;
        display:inline-flex;
        align-items:center;
        gap:8px;
        overflow:hidden;

        box-shadow:
            0 10px 26px rgba(0,0,0,.45),
            inset 0 1px 0 rgba(255,255,255,.18);
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        }

        /* liquid shine */
        .btn::before{
        content:"";
        position:absolute;
        left:var(--lx,35%);
        top:var(--ly,20%);
        width:220px;
        height:140px;
        transform: translate(-50%,-50%);
        background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.30), transparent 62%);
        opacity:.55;
        pointer-events:none;
        }

        .btn:hover{
        transform:translateY(-1px) scale(1.01);
        background:rgba(255,255,255,.09);
        border-color:rgba(255,255,255,.22);
        box-shadow:
            0 16px 38px rgba(0,0,0,.55),
            inset 0 1px 0 rgba(255,255,255,.20);
        }

        .btn:active{
        transform:translateY(0) scale(.985);
        box-shadow:
            0 10px 24px rgba(0,0,0,.50),
            inset 0 2px 12px rgba(0,0,0,.38);
        }

        .btn-primary{
        border-color: rgba(110,168,255,.30);
        background:linear-gradient(180deg, rgba(110,168,255,.24), rgba(155,123,255,.14));
        }

        .btn-danger{
        border-color: rgba(255,77,87,.35);
        background:linear-gradient(180deg, rgba(255,77,87,.22), rgba(255,77,87,.12));
        }

        .btn-success{
        border-color: rgba(43,213,118,.35);
        background:linear-gradient(180deg, rgba(43,213,118,.18), rgba(43,213,118,.10));
        }

        .btn-sm{
        padding:8px 12px;
        font-size:12px;
        }

        /* =========================================================
        SIDE MENU (dock-like)
        ========================================================= */
        .admin-menu{
        position:fixed;
        top:66px;
        right:14px;
        bottom:14px;
        width:250px;
        padding:12px 10px;
        overflow-y:auto;

        background:rgba(12,14,22,.52);
        border:1px solid rgba(255,255,255,.10);
        border-radius:var(--radius);
        box-shadow:var(--shadow);
        }

        .admin-menu::-webkit-scrollbar{ width:8px; }
        .admin-menu::-webkit-scrollbar-thumb{
        background:rgba(255,255,255,.10);
        border-radius:999px;
        }

        .menu-item{
        display:flex;
        align-items:center;
        gap:12px;
        padding:12px 12px;
        margin:7px 6px;
        border-radius:var(--pill);
        border:1px solid transparent;
        color:var(--text-light);
        text-decoration:none;
        transition:transform .18s ease, background .2s ease, border-color .2s ease;
        position:relative;
        }

        .menu-item:hover{
        background:rgba(255,255,255,.06);
        border-color:rgba(255,255,255,.10);
        color:var(--text);
        transform:translateY(-1px);
        }

        .menu-item.active{
        background:linear-gradient(180deg, rgba(110,168,255,.18), rgba(155,123,255,.10));
        border-color:rgba(110,168,255,.28);
        color:var(--text);
        box-shadow:0 12px 30px rgba(110,168,255,.12);
        }

        .menu-item.active::after{
        content:"";
        position:absolute;
        left:10px;
        top:50%;
        width:7px;
        height:7px;
        transform:translateY(-50%);
        border-radius:999px;
        background:linear-gradient(180deg, var(--primary), rgba(155,123,255,1));
        box-shadow:0 0 18px rgba(110,168,255,.25);
        }

        /* =========================================================
        MAIN
        ========================================================= */
        .wrapper{
        margin-top:66px;
        margin-right:290px;
        padding:22px;
        min-height:calc(100vh - 66px);
        }

        .page-title{
        font-size:22px;
        font-weight:950;
        margin:10px 0 18px 0;
        line-height:1.3;
        }

        /* =========================================================
        LAYOUT (content-wrapper etc)
        ========================================================= */
        .content-wrapper{
        display:flex;
        gap:16px;
        }

        @media (max-width:1024px){
        .content-wrapper{ flex-direction:column; }
        }

        .left-column{
        flex:1;
        min-width:300px;
        }

        .right-column{
        flex:2;
        }

        /* =========================================================
        CARDS
        ========================================================= */
        .card{
        margin-bottom:18px;
        }

        .card-header{
        padding:18px;
        border-bottom:1px solid rgba(255,255,255,.10);
        font-size:15px;
        font-weight:950;
        display:flex;
        align-items:center;
        gap:10px;
        }

        .card-body{ padding:18px; }

        /* =========================================================
        FORMS
        ========================================================= */
        .form-group{ margin-bottom:16px; }

        .form-label{
        display:block;
        margin-bottom:8px;
        font-weight:950;
        color:var(--text-light);
        font-size:12.5px;
        }

        .form-control{
        width:100%;
        padding:11px 12px;
        border:1px solid rgba(255,255,255,.12);
        border-radius:16px;
        font-size:13px;
        color:var(--text);
        background:rgba(255,255,255,.06);
        transition:border-color .18s ease, box-shadow .18s ease, background .18s ease;
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        box-shadow: inset 0 1px 0 rgba(255,255,255,.12);
        }

        .form-control:focus{
        border-color:rgba(110,168,255,.45);
        outline:none;
        box-shadow:0 0 0 3px rgba(110,168,255,.16);
        background:rgba(255,255,255,.08);
        }

        textarea.form-control{
        min-height:90px;
        resize:vertical;
        }

        /* =========================================================
        TABLE
        ========================================================= */
        .table-container{ overflow-x:auto; }

        .wp-list-table{
        width:100%;
        border-collapse:collapse;
        background:transparent;
        }

        .wp-list-table thead th{
        background:rgba(255,255,255,.04);
        padding:12px;
        text-align:left;
        font-weight:950;
        border-bottom:1px solid rgba(255,255,255,.10);
        color:rgba(255,255,255,.48);
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        }

        .wp-list-table tbody tr{
        border-bottom:1px solid rgba(255,255,255,.08);
        }

        .wp-list-table tbody tr:hover{
        background:rgba(255,255,255,.05);
        }

        .wp-list-table td{
        padding:12px;
        vertical-align:middle;
        color:var(--text-light);
        }

        /* =========================================================
        BADGES
        ========================================================= */
        .badge{
        display:inline-flex;
        align-items:center;
        padding:6px 10px;
        border-radius:var(--pill);
        font-size:12px;
        font-weight:950;
        border:1px solid rgba(255,255,255,.12);
        background:rgba(255,255,255,.06);
        color:var(--text);
        }

        .badge-primary{
        background: rgba(110,168,255,.12);
        border-color: rgba(110,168,255,.28);
        }

        .badge-success{
        background: rgba(43,213,118,.12);
        border-color: rgba(43,213,118,.28);
        }

        /* =========================================================
        ACTION BUTTONS
        ========================================================= */
        .action-buttons{ display:flex; gap:8px; }

        /* =========================================================
        CUSTOMER ID (glass chip)
        ========================================================= */
        .customer-id{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        background: rgba(255,255,255,.06);
        padding: 8px 12px;
        border-radius: 16px;
        font-size: 12.5px;
        border: 1px solid rgba(255,255,255,.12);
        color: var(--text);
        }

        /* =========================================================
        MODAL
        ========================================================= */
        .modal{
        display:none;
        position:fixed;
        inset:0;
        background: rgba(0,0,0,.55);
        z-index:2000;
        align-items:center;
        justify-content:center;
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        }

        .modal-content{
        width:92%;
        max-width:520px;
        max-height:90vh;
        overflow-y:auto;
        background:rgba(255,255,255,.055);
        border:1px solid rgba(255,255,255,.12);
        border-radius:26px;
        box-shadow:var(--shadow);
        }

        .modal-header{
        padding:18px;
        border-bottom:1px solid rgba(255,255,255,.10);
        display:flex;
        justify-content:space-between;
        align-items:center;
        }

        .modal-body{ padding:18px; }

        .modal-footer{
        padding:18px;
        border-top:1px solid rgba(255,255,255,.10);
        display:flex;
        justify-content:flex-end;
        gap:10px;
        }

        /* =========================================================
        RESPONSIVE
        ========================================================= */
        @media (max-width:768px){
        .admin-menu{ width:86px; right:10px; }
        .admin-menu .menu-text{ display:none; }
        .wrapper{ margin-right:110px; }
        }

        .no-results{
        text-align:center;
        padding:40px 20px;
        color:var(--text-light);
        }

        .no-results i{
        font-size:46px;
        margin-bottom:14px;
        opacity:.55;
        }

        /* iOS focus ring */
        a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{
        outline:none;
        box-shadow:0 0 0 3px rgba(110,168,255,.18);
        border-color:rgba(110,168,255,.45);
        }

    </style>
</head>
<body>
    <!-- Top Bar -->
    <div class="top-bar">
        <div class="logo">
            <span>👥</span>
            <span>License Manager</span>
        </div>
        <div class="user-menu">
            <a href="{{ url_for('dashboard') }}" class="btn btn-primary">
                <span>🏠</span>
                <span>Dashboard</span>
            </a>
            <a href="{{ url_for('logout') }}" class="btn btn-danger">
                <span>🚪</span>
                <span>Logout</span>
            </a>
        </div>
    </div>
    
    <!-- Side Menu -->
    <nav class="admin-menu">
        <a href="{{ url_for('dashboard') }}" class="menu-item">
            <span>📊</span>
            <span class="menu-text">Dashboard</span>
        </a>
        <a href="{{ url_for('customers_page') }}" class="menu-item active">
            <span>👥</span>
            <span class="menu-text">Customers</span>
        </a>
        <a href="{{ url_for('products') }}" class="menu-item">
            <span>📦</span>
            <span class="menu-text">Products</span>
        </a>
        <a href="{{ url_for('blacklist_page') }}" class="menu-item">
            <span>🛑</span>
            <span class="menu-text">Blacklist</span>
        </a>
    </nav>
    
    <!-- Main Content -->
    <div class="wrapper">
        <h1 class="page-title">Customer Management</h1>
        
        <div class="content-wrapper">
            <!-- Left Column: Add Customer -->
            <div class="left-column">
                <div class="card">
                    <div class="card-header">
                        <span>➕</span>
                        <span>Add New Customer</span>
                    </div>
                    <div class="card-body">
                        <form method="post" action="{{ url_for('add_customer') }}">
                            <div class="form-group">
                                <label class="form-label">Customer ID</label>
                                <input type="text" name="customer_id" class="form-control" placeholder="e.g., cust_001" required>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Customer Name</label>
                                <input type="text" name="customer_name" class="form-control" placeholder="Full name" required>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Email Address</label>
                                <input type="email" name="customer_email" class="form-control" placeholder="customer@example.com">
                            </div>
                            <div class="form-group">
                                <label class="form-label">Discord ID</label>
                                <input type="text" name="discord_id" class="form-control" placeholder="Discord username or ID">
                            </div>
                            <div class="form-group">
                                <label class="form-label">Notes</label>
                                <textarea name="notes" class="form-control" placeholder="Additional notes about this customer"></textarea>
                            </div>
                            <button type="submit" class="btn btn-success" style="width: 100%;">
                                <span>✅</span>
                                <span>Add Customer</span>
                            </button>
                        </form>
                    </div>
                </div>
            </div>
            
            <!-- Right Column: Customers List -->
            <div class="right-column">
                <div class="card">
                    <div class="card-header">
                        <span>📋</span>
                        <span>Customers List ({{ customers|length }})</span>
                    </div>
                    <div class="card-body">
                        {% if customers %}
                        <div class="table-container">
                            <table class="wp-list-table">
                                <thead>
                                    <tr>
                                        <th>Customer ID</th>
                                        <th>Name</th>
                                        <th>Email</th>
                                        <th>Discord</th>
                                        <th>Licenses</th>
                                        <th>Created</th>
                                        <th>Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for customer_id, customer in customers.items() %}
                                    <tr>
                                        <td>
                                            <span class="customer-id">{{ customer_id }}</span>
                                        </td>
                                        <td>
                                            <strong>{{ customer.name }}</strong>
                                        </td>
                                        <td>{{ customer.email or '-' }}</td>
                                        <td>{{ customer.discord_id or '-' }}</td>
                                        <td>
                                            <span class="badge badge-primary">{{ customer.license_count }}</span>
                                        </td>
                                        <td>{{ (customer.created | int) | datetime }}</td>
                                        <td>
                                            <div class="action-buttons">
                                                <button type="button" class="btn btn-primary btn-sm" onclick="openEditModal('{{ customer_id }}')">
                                                    <span>✏️</span>
                                                    <span>Edit</span>
                                                </button>
                                                <a href="{{ url_for('remove_customer', customer_id=customer_id) }}" 
                                                   class="btn btn-danger btn-sm"
                                                   onclick="return confirm('Are you sure you want to delete this customer?')">
                                                    <span>🗑️</span>
                                                    <span>Delete</span>
                                                </a>
                                            </div>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <div class="no-results">
                            <span>👥</span>
                            <h3>No Customers Found</h3>
                            <p>Add your first customer using the form on the left.</p>
                        </div>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Edit Modal -->
    <div class="modal" id="editModal">
        <div class="modal-content">
            <div class="modal-header">
                <h2>Edit Customer</h2>
                <button type="button" onclick="closeEditModal()" style="background: none; border: none; font-size: 20px; cursor: pointer;">×</button>
            </div>
            <form method="post" action="" id="editForm">
                <div class="modal-body">
                    <div class="form-group">
                        <label class="form-label">Customer Name</label>
                        <input type="text" name="customer_name" class="form-control" required>
                    </div>
                    <div class="form-group">
                        <label class="form-label">Email Address</label>
                        <input type="email" name="customer_email" class="form-control">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Discord ID</label>
                        <input type="text" name="discord_id" class="form-control">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Notes</label>
                        <textarea name="notes" class="form-control"></textarea>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn" onclick="closeEditModal()">Cancel</button>
                    <button type="submit" class="btn btn-success">Save Changes</button>
                </div>
            </form>
        </div>
    </div>
    
    <script>
        function openEditModal(customerId) {
            // In a real application, you would fetch customer data via API
            // For now, we'll just set the form action
            document.getElementById('editForm').action = "/customer/edit/" + customerId;
            document.getElementById('editModal').style.display = 'flex';
        }
        
        function closeEditModal() {
            document.getElementById('editModal').style.display = 'none';
        }
        
        // Close modal when clicking outside
        document.getElementById('editModal').addEventListener('click', function(e) {
            if (e.target === this) {
                closeEditModal();
            }
        });
    </script>
</body>
</html>
"""

PRODUCTS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Products - License Manager</title>
    <style>
        *{
        margin:0;
        padding:0;
        box-sizing:border-box;
        font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Segoe UI",Roboto,Oxygen,Ubuntu,sans-serif;
        }

        :root{
        /* background */
        --bg0:#05060a;
        --bg1:#070a12;

        /* text */
        --text:rgba(255,255,255,.92);
        --text-light:rgba(255,255,255,.62);

        /* liquid glass */
        --glass:rgba(255,255,255,.06);
        --glass2:rgba(255,255,255,.10);
        --border:rgba(255,255,255,.12);
        --border2:rgba(255,255,255,.18);

        /* accents */
        --primary:#6ea8ff;
        --primary-dark:#4e8cff;
        --success:#2bd576;
        --danger:#ff4d57;
        --warning:#ffcc47;

        /* shape */
        --radius:22px;
        --radius2:18px;
        --pill:999px;

        /* blur + shadows */
        --blur:22px;
        --blur-strong:34px;
        --shadow:0 24px 70px rgba(0,0,0,.65);
        --shadow-soft:0 12px 28px rgba(0,0,0,.45);

        color-scheme:dark;
        }

        body{
        background:
            radial-gradient(1200px 800px at 20% 10%, rgba(110,168,255,.20), transparent 55%),
            radial-gradient(1000px 700px at 85% 20%, rgba(155,123,255,.16), transparent 55%),
            radial-gradient(900px 650px at 45% 95%, rgba(43,213,118,.10), transparent 60%),
            linear-gradient(180deg,var(--bg0) 0%,var(--bg1) 100%);
        color:var(--text);
        line-height:1.5;
        }

        /* tiny noise = makes liquid feel real */
        body::before{
        content:"";
        position:fixed;
        inset:0;
        pointer-events:none;
        opacity:.10;
        mix-blend-mode:overlay;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='260'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='260' height='260' filter='url(%23n)' opacity='.55'/%3E%3C/svg%3E");
        }

        /* ================== LIQUID SURFACE CORE ================== */
        .card,
        .top-bar,
        .admin-menu,
        .help-box{
        backdrop-filter: blur(var(--blur-strong)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur-strong)) saturate(170%);
        }

        .card,
        .help-box{
        position:relative;
        overflow:hidden;
        border:1px solid var(--border);
        border-radius:var(--radius);
        background:rgba(255,255,255,.055);
        box-shadow:var(--shadow-soft);
        }

        /* specular highlights + refraction vibe */
        .card::before,
        .help-box::before{
        content:"";
        position:absolute;
        inset:-45% -25%;
        background:
            radial-gradient(640px 260px at 30% 10%, rgba(255,255,255,.22), transparent 62%),
            radial-gradient(520px 260px at 80% 0%, rgba(255,255,255,.10), transparent 60%),
            radial-gradient(560px 380px at 50% 120%, rgba(110,168,255,.09), transparent 60%);
        opacity:.62;
        transform:rotate(-8deg);
        pointer-events:none;
        }

        /* glossy inner edge */
        .card::after,
        .help-box::after{
        content:"";
        position:absolute;
        inset:0;
        border-radius:inherit;
        pointer-events:none;
        box-shadow:
            inset 0 1px 0 rgba(255,255,255,.18),
            inset 0 -1px 0 rgba(0,0,0,.42);
        }

        /* ================== TOP BAR ================== */
        .top-bar{
        background:rgba(12,14,22,.55);
        color:var(--text);
        padding:0 18px;
        height:66px;
        display:flex;
        align-items:center;
        justify-content:space-between;
        border-bottom:1px solid rgba(255,255,255,.10);
        position:fixed;
        top:0; right:0; left:0;
        z-index:1000;
        }

        .top-bar::before{
        content:"";
        position:absolute;
        inset:0;
        background:
            radial-gradient(700px 260px at 25% 0%, rgba(255,255,255,.12), transparent 60%),
            radial-gradient(700px 260px at 80% 0%, rgba(155,123,255,.10), transparent 60%);
        pointer-events:none;
        opacity:.65;
        }

        .logo{
        position:relative;
        display:flex;
        align-items:center;
        gap:10px;
        font-size:18px;
        font-weight:850;
        letter-spacing:.2px;
        }

        .user-menu{
        position:relative;
        display:flex;
        align-items:center;
        gap:10px;
        }

        /* ================== BUTTONS (REAL iOS LIQUID) ================== */
        .btn{
        position:relative;
        padding:10px 14px;
        border-radius:var(--pill);
        border:1px solid rgba(255,255,255,.14);
        background:rgba(255,255,255,.07);
        color:var(--text);
        cursor:pointer;
        font-size:13px;
        font-weight:900;
        transition:transform .14s ease, box-shadow .18s ease, border-color .18s ease, background .18s ease;
        text-decoration:none;
        display:inline-flex;
        align-items:center;
        gap:8px;
        overflow:hidden;
        box-shadow:
            0 10px 26px rgba(0,0,0,.45),
            inset 0 1px 0 rgba(255,255,255,.18);
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        }

        /* glossy sweep (the iPhone “liquid shine”) */
        .btn::before{
        content:"";
        position:absolute;
        left:var(--lx,35%);
        top:var(--ly,20%);
        width:220px;
        height:140px;
        transform: translate(-50%,-50%);
        background: radial-gradient(circle at 30% 30%, rgba(255,255,255,.30), transparent 62%);
        opacity:.55;
        pointer-events:none;
        filter: blur(1px);
        }

        /* micro edge highlight */
        .btn::after{
        content:"";
        position:absolute;
        inset:0;
        border-radius:inherit;
        pointer-events:none;
        box-shadow: inset 0 1px 0 rgba(255,255,255,.18);
        }

        .btn:hover{
        transform:translateY(-1px) scale(1.01);
        background:rgba(255,255,255,.09);
        border-color:rgba(255,255,255,.22);
        box-shadow:
            0 16px 38px rgba(0,0,0,.55),
            inset 0 1px 0 rgba(255,255,255,.20);
        }

        .btn:active{
        transform:translateY(0) scale(.985);
        box-shadow:
            0 10px 24px rgba(0,0,0,.50),
            inset 0 2px 12px rgba(0,0,0,.38);
        }

        .btn-primary{
        border-color: rgba(110,168,255,.30);
        background:linear-gradient(180deg, rgba(110,168,255,.24), rgba(155,123,255,.14));
        }

        .btn-danger{
        border-color: rgba(255,77,87,.35);
        background:linear-gradient(180deg, rgba(255,77,87,.22), rgba(255,77,87,.12));
        }

        .btn-success{
        border-color: rgba(43,213,118,.35);
        background:linear-gradient(180deg, rgba(43,213,118,.18), rgba(43,213,118,.10));
        }

        /* ================== SIDE MENU (DOCK-LIKE) ================== */
        .admin-menu{
        position:fixed;
        top:66px;
        right:14px;
        bottom:14px;
        width:250px;
        background:rgba(12,14,22,.52);
        border:1px solid rgba(255,255,255,.10);
        border-radius:var(--radius);
        padding:12px 10px;
        overflow-y:auto;
        box-shadow:var(--shadow);
        }

        .admin-menu::-webkit-scrollbar{ width:8px; }
        .admin-menu::-webkit-scrollbar-thumb{
        background:rgba(255,255,255,.10);
        border-radius:999px;
        }

        .menu-item{
        display:flex;
        align-items:center;
        gap:12px;
        padding:12px 12px;
        color:var(--text-light);
        text-decoration:none;
        border-radius:var(--pill);
        border:1px solid transparent;
        transition:transform .18s ease, background .2s ease, border-color .2s ease;
        margin:7px 6px;
        position:relative;
        }

        .menu-item:hover{
        background:rgba(255,255,255,.06);
        border-color:rgba(255,255,255,.10);
        color:var(--text);
        transform:translateY(-1px);
        }

        .menu-item.active{
        background:linear-gradient(180deg, rgba(110,168,255,.18), rgba(155,123,255,.10));
        border-color:rgba(110,168,255,.28);
        color:var(--text);
        box-shadow:0 12px 30px rgba(110,168,255,.12);
        }

        .menu-item.active::after{
        content:"";
        position:absolute;
        left:10px;
        top:50%;
        width:7px;
        height:7px;
        transform:translateY(-50%);
        border-radius:999px;
        background:linear-gradient(180deg, var(--primary), rgba(155,123,255,1));
        box-shadow:0 0 18px rgba(110,168,255,.25);
        }

        /* ================== MAIN CONTENT ================== */
        .wrapper{
        margin-top:66px;
        margin-right:290px;
        padding:22px;
        min-height:calc(100vh - 66px);
        }

        .page-title{
        font-size:22px;
        font-weight:900;
        margin:10px 0 18px 0;
        line-height:1.3;
        }

        /* ================== CARDS ================== */
        .card{
        margin-bottom:18px;
        }

        .card-header{
        padding:18px;
        border-bottom:1px solid rgba(255,255,255,.10);
        font-size:15px;
        font-weight:900;
        display:flex;
        align-items:center;
        gap:10px;
        }

        .card-body{ padding:18px; }

        /* ================== FORMS ================== */
        .form-row{
        display:flex;
        gap:14px;
        margin-bottom:16px;
        }

        @media (max-width:768px){
        .form-row{ flex-direction:column; }
        }

        .form-group{ flex:1; }

        .form-label{
        display:block;
        margin-bottom:8px;
        font-weight:900;
        color:var(--text-light);
        font-size:12.5px;
        }

        .form-control{
        width:100%;
        padding:11px 12px;
        border:1px solid rgba(255,255,255,.12);
        border-radius:16px;
        font-size:13px;
        color:var(--text);
        background:rgba(255,255,255,.06);
        transition:border-color .18s ease, box-shadow .18s ease, background .18s ease;
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        box-shadow: inset 0 1px 0 rgba(255,255,255,.12);
        }

        .form-control::placeholder{ color:rgba(255,255,255,.30); }

        .form-control:focus{
        border-color:rgba(110,168,255,.45);
        outline:none;
        box-shadow:0 0 0 3px rgba(110,168,255,.16);
        background:rgba(255,255,255,.08);
        }

        /* ================== TABLE ================== */
        .table-container{ overflow-x:auto; }

        .wp-list-table{
        width:100%;
        border-collapse:collapse;
        background:transparent;
        }

        .wp-list-table thead th{
        background:rgba(255,255,255,.04);
        padding:12px;
        text-align:left;
        font-weight:900;
        border-bottom:1px solid rgba(255,255,255,.10);
        color:rgba(255,255,255,.48);
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        }

        .wp-list-table tbody tr{
        border-bottom:1px solid rgba(255,255,255,.08);
        }

        .wp-list-table tbody tr:hover{
        background:rgba(255,255,255,.05);
        }

        .wp-list-table td{
        padding:12px;
        vertical-align:middle;
        color:var(--text-light);
        }

        /* ================== BADGES ================== */
        .badge{
        display:inline-flex;
        align-items:center;
        padding:6px 10px;
        border-radius:var(--pill);
        font-size:12px;
        font-weight:950;
        border:1px solid rgba(255,255,255,.12);
        background:rgba(255,255,255,.06);
        color:var(--text);
        }

        .badge-success{
        background: rgba(43,213,118,.12);
        border-color: rgba(43,213,118,.28);
        }

        .badge-warning{
        background: rgba(255,204,71,.12);
        border-color: rgba(255,204,71,.28);
        color: rgba(255,255,255,.90);
        }

        /* ================== PRODUCT ID ================== */
        .product-id{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        background: rgba(255,255,255,.06);
        padding: 8px 12px;
        border-radius: 16px;
        font-size: 12.5px;
        border: 1px solid rgba(255,255,255,.12);
        color: var(--text);
        }

        /* ================== ACTION BUTTONS ================== */
        .action-buttons{
        display:flex;
        gap:8px;
        }

        .btn-sm{
        padding:8px 12px;
        font-size:12px;
        }

        /* ================== RESPONSIVE ================== */
        @media (max-width:768px){
        .admin-menu{
            width: 86px;
            right: 10px;
        }
        .admin-menu .menu-text{ display:none; }
        .wrapper{ margin-right: 110px; }
        }

        .no-results{
        text-align:center;
        padding:40px 20px;
        color:var(--text-light);
        }

        .no-results i{
        font-size:46px;
        margin-bottom:14px;
        opacity:.55;
        }

        .help-box{
        border-radius:var(--radius);
        padding:16px;
        margin-top:18px;
        border:1px solid rgba(110,168,255,.22);
        background: rgba(110,168,255,.08);
        }

        /* iOS focus ring */
        a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{
        outline:none;
        box-shadow:0 0 0 3px rgba(110,168,255,.18);
        border-color:rgba(110,168,255,.45);
        }

    </style>
</head>
<body>
    <!-- Top Bar -->
    <div class="top-bar">
        <div class="logo">
            <span>📦</span>
            <span>License Manager</span>
        </div>
        <div class="user-menu">
            <a href="{{ url_for('dashboard') }}" class="btn btn-primary">
                <span>🏠</span>
                <span>Dashboard</span>
            </a>
            <a href="{{ url_for('logout') }}" class="btn btn-danger">
                <span>🚪</span>
                <span>Logout</span>
            </a>
        </div>
</div>
    
    <!-- Side Menu -->
    <nav class="admin-menu">
        <a href="{{ url_for('dashboard') }}" class="menu-item">
            <span>📊</span>
            <span class="menu-text">Dashboard</span>
        </a>
        <a href="{{ url_for('customers_page') }}" class="menu-item">
            <span>👥</span>
            <span class="menu-text">Customers</span>
        </a>
        <a href="{{ url_for('products') }}" class="menu-item active">
            <span>📦</span>
            <span class="menu-text">Products</span>
        </a>
        <a href="{{ url_for('blacklist_page') }}" class="menu-item">
            <span>🛑</span>
            <span class="menu-text">Blacklist</span>
        </a>
    </nav>
    
    <!-- Main Content -->
    <div class="wrapper">
        <h1 class="page-title">Product Management</h1>
        
        <!-- Add Product Form -->
        <div class="card">
            <div class="card-header">
                <span>➕</span>
                <span>Add New Product</span>
            </div>
            <div class="card-body">
                <form method="post" action="{{ url_for('add_product') }}">
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Product ID</label>
                            <input type="text" name="product_id" class="form-control" placeholder="e.g., product_pro" required>
                        </div>
                        <div class="form-group">
                            <label class="form-label">Product Name</label>
                            <input type="text" name="product_name" class="form-control" placeholder="Full product name" required>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">Price</label>
                            <input type="text" name="price" class="form-control" placeholder="e.g., $99.99 or Free">
                        </div>
                        <div class="form-group">
                            <label class="form-label">Notes</label>
                            <input type="text" name="notes" class="form-control" placeholder="Optional notes">
                        </div>
                    </div>
                    <button type="submit" class="btn btn-success">
                        <span>✅</span>
                        <span>Save Product</span>
                    </button>
                </form>
            </div>
        </div>
        
        <!-- Products List -->
        <div class="card">
            <div class="card-header">
                <span>📋</span>
                <span>Products List ({{ products|length }})</span>
            </div>
            <div class="card-body">
                {% if products %}
                <div class="table-container">
                    <table class="wp-list-table">
                        <thead>
                            <tr>
                                <th>Product ID</th>
                                <th>Product Name</th>
                                <th>Price</th>
                                <th>Created</th>
                                <th>Notes</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for pid, pdata in products.items() %}
                            <tr>
                                <td>
                                    <span class="product-id">{{ pid }}</span>
                                </td>
                                <td>
                                    <strong>{{ pdata.name }}</strong>
                                </td>
                                <td>
                                    {% if pdata.price %}
                                    <span class="badge badge-success">{{ pdata.price }}</span>
                                    {% else %}
                                    <span class="badge badge-warning">Not set</span>
                                    {% endif %}
                                </td>
                                <td>{{ (pdata.created | int) | datetime }}</td>
                                <td>{{ pdata.notes or '-' }}</td>
                                <td>
                                    <div class="action-buttons">
                                        <a href="{{ url_for('remove_product', product_id=pid) }}" 
                                           class="btn btn-danger btn-sm"
                                           onclick="return confirm('Are you sure you want to delete this product?')">
                                            <span>🗑️</span>
                                            <span>Delete</span>
                                        </a>
                                    </div>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="no-results">
                    <span>📦</span>
                    <h3>No Products Found</h3>
                    <p>Add your first product using the form above.</p>
                </div>
                {% endif %}
            </div>
        </div>
        
        <!-- Help Section -->
        <div class="card">
            <div class="card-header">
                <span>❓</span>
                <span>Usage Guide</span>
            </div>
            <div class="card-body">
                <div class="help-box">
                    <strong>Important Notes:</strong>
                    <ul>
                        <li>Product ID must be unique and in English (no spaces)</li>
                        <li>Each license must be associated with a product</li>
                        <li>Deleted products do not affect existing licenses</li>
                        <li>Use product IDs that are easy to remember and type</li>
                        <li>Price field is optional and can contain any text</li>
                    </ul>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

BLACKLIST_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Blacklist - License Manager</title>
    <style>
        *{
        margin:0;
        padding:0;
        box-sizing:border-box;
        font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Segoe UI",Roboto,Oxygen,Ubuntu,sans-serif;
        }

        :root{
        --bg:#05060a;
        --bg2:#070a12;

        --text:rgba(255,255,255,.92);
        --text-light:rgba(255,255,255,.62);

        --glass:rgba(255,255,255,.07);
        --glass-2:rgba(255,255,255,.10);
        --border:rgba(255,255,255,.12);
        --border-2:rgba(255,255,255,.18);

        --primary:#6ea8ff;
        --primary-dark:#4e8cff;

        --success:#2bd576;
        --danger:#ff4d57;
        --warning:#ffcc47;

        --shadow:0 22px 60px rgba(0,0,0,.65);
        --shadow-soft:0 12px 28px rgba(0,0,0,.45);

        --radius:22px;
        --radius2:18px;
        --pill:999px;
        --blur:22px;
        --blur-strong:34px;

        color-scheme:dark;
        }

        body{
        background:
            radial-gradient(1200px 800px at 20% 10%, rgba(110,168,255,.18), transparent 55%),
            radial-gradient(1000px 700px at 85% 20%, rgba(155,123,255,.14), transparent 55%),
            radial-gradient(900px 650px at 45% 95%, rgba(43,213,118,.10), transparent 60%),
            linear-gradient(180deg,var(--bg) 0%,var(--bg2) 100%);
        color:var(--text);
        line-height:1.5;
        }

        body::before{
        content:"";
        position:fixed;
        inset:0;
        pointer-events:none;
        opacity:.10;
        mix-blend-mode:overlay;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='260'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='260' height='260' filter='url(%23n)' opacity='.55'/%3E%3C/svg%3E");
        }

        /* ===== Liquid surfaces (shared feel) ===== */
        .top-bar,
        .admin-menu,
        .card,
        .help-box,
        .alert,
        .ip-address,
        .reason-box{
        backdrop-filter: blur(var(--blur-strong)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur-strong)) saturate(170%);
        }

        .card,
        .help-box,
        .alert{
        position:relative;
        overflow:hidden;
        border:1px solid var(--border);
        border-radius:var(--radius);
        background:rgba(255,255,255,.06);
        box-shadow:var(--shadow-soft);
        }

        .card::before,
        .help-box::before,
        .alert::before{
        content:"";
        position:absolute;
        inset:-40% -20%;
        background:
            radial-gradient(620px 260px at 30% 10%, rgba(255,255,255,.20), transparent 60%),
            radial-gradient(520px 260px at 80% 0%, rgba(255,255,255,.10), transparent 55%),
            radial-gradient(520px 360px at 50% 120%, rgba(110,168,255,.08), transparent 55%);
        opacity:.62;
        transform:rotate(-8deg);
        pointer-events:none;
        }

        .card::after,
        .help-box::after,
        .alert::after{
        content:"";
        position:absolute;
        inset:0;
        border-radius:inherit;
        pointer-events:none;
        box-shadow:
            inset 0 1px 0 rgba(255,255,255,.18),
            inset 0 -1px 0 rgba(0,0,0,.42);
        }

        /* ===== Top Bar ===== */
        .top-bar{
        background:rgba(12,14,22,.55);
        color:var(--text);
        padding:0 18px;
        height:66px;
        display:flex;
        align-items:center;
        justify-content:space-between;
        border-bottom:1px solid rgba(255,255,255,.10);
        box-shadow:0 1px 0 rgba(255,255,255,.06);
        position:fixed;
        top:0; right:0; left:0;
        z-index:1000;
        }

        .top-bar::before{
        content:"";
        position:absolute;
        inset:0;
        background:
            radial-gradient(700px 260px at 25% 0%, rgba(255,255,255,.12), transparent 60%),
            radial-gradient(700px 260px at 80% 0%, rgba(155,123,255,.10), transparent 60%);
        pointer-events:none;
        opacity:.6;
        }

        .logo{
        position:relative;
        display:flex;
        align-items:center;
        gap:10px;
        font-size:18px;
        font-weight:800;
        letter-spacing:.2px;
        }

        .user-menu{
        position:relative;
        display:flex;
        align-items:center;
        gap:10px;
        }

        /* ===== Buttons (iOS liquid) ===== */
        .btn{
        position:relative;
        padding:10px 14px;
        border-radius:var(--pill);
        border:1px solid rgba(255,255,255,.14);
        background:rgba(255,255,255,.07);
        color:var(--text);
        cursor:pointer;
        font-size:13px;
        font-weight:800;
        transition:transform .14s ease, box-shadow .18s ease, border-color .18s ease, background .18s ease;
        text-decoration:none;
        display:inline-flex;
        align-items:center;
        gap:8px;
        overflow:hidden;
        box-shadow:
            0 10px 26px rgba(0,0,0,.45),
            inset 0 1px 0 rgba(255,255,255,.18);
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        }

        .btn::before{
        content:"";
        position:absolute;
        inset:-60% -30%;
        background:
            radial-gradient(260px 120px at 30% 20%, rgba(255,255,255,.26), transparent 60%),
            radial-gradient(240px 120px at 75% 10%, rgba(255,255,255,.12), transparent 60%);
        opacity:.55;
        transform:rotate(-12deg);
        pointer-events:none;
        }

        .btn:hover{
        transform:translateY(-1px) scale(1.01);
        background:rgba(255,255,255,.09);
        border-color:rgba(255,255,255,.22);
        box-shadow:
            0 16px 36px rgba(0,0,0,.55),
            inset 0 1px 0 rgba(255,255,255,.20);
        }

        .btn:active{
        transform:translateY(0) scale(.985);
        box-shadow:
            0 10px 24px rgba(0,0,0,.50),
            inset 0 2px 10px rgba(0,0,0,.35);
        }

        .btn-primary{
        border-color: rgba(110,168,255,.30);
        background:linear-gradient(180deg, rgba(110,168,255,.22), rgba(155,123,255,.14));
        }

        .btn-primary:hover{
        background:linear-gradient(180deg, rgba(110,168,255,.26), rgba(155,123,255,.16));
        }

        .btn-danger{
        border-color: rgba(255,77,87,.35);
        background:linear-gradient(180deg, rgba(255,77,87,.22), rgba(255,77,87,.12));
        }

        .btn-success{
        border-color: rgba(43,213,118,.35);
        background:linear-gradient(180deg, rgba(43,213,118,.18), rgba(43,213,118,.10));
        }

        /* ===== Side Menu (liquid dock style) ===== */
        .admin-menu{
        position:fixed;
        top:66px;
        right:14px;
        bottom:14px;
        width:250px;
        background:rgba(12,14,22,.52);
        border:1px solid rgba(255,255,255,.10);
        border-radius:var(--radius);
        padding:12px 10px;
        overflow-y:auto;
        box-shadow:var(--shadow);
        }

        .admin-menu::-webkit-scrollbar{ width:8px; }
        .admin-menu::-webkit-scrollbar-thumb{
        background:rgba(255,255,255,.10);
        border-radius:999px;
        }

        .menu-item{
        display:flex;
        align-items:center;
        gap:12px;
        padding:12px 12px;
        color:var(--text-light);
        text-decoration:none;
        border-radius:var(--pill);
        border:1px solid transparent;
        transition:transform .18s ease, background .2s ease, border-color .2s ease;
        margin:7px 6px;
        position:relative;
        }

        .menu-item:hover{
        background:rgba(255,255,255,.06);
        border-color:rgba(255,255,255,.10);
        color:var(--text);
        transform:translateY(-1px);
        }

        .menu-item.active{
        background:linear-gradient(180deg, rgba(110,168,255,.18), rgba(155,123,255,.10));
        border-color:rgba(110,168,255,.28);
        color:var(--text);
        box-shadow:0 12px 30px rgba(110,168,255,.12);
        }

        .menu-item.active::after{
        content:"";
        position:absolute;
        left:10px;
        top:50%;
        width:7px;
        height:7px;
        transform:translateY(-50%);
        border-radius:999px;
        background:linear-gradient(180deg, var(--primary), rgba(155,123,255,1));
        box-shadow:0 0 18px rgba(110,168,255,.25);
        }

        /* ===== Main Content ===== */
        .wrapper{
        margin-top:66px;
        margin-right:290px;
        padding:22px;
        min-height:calc(100vh - 66px);
        }

        .page-title{
        font-size:22px;
        font-weight:850;
        margin:10px 0 18px 0;
        line-height:1.3;
        }

        /* ===== Card layout ===== */
        .card-header{
        padding:18px;
        border-bottom:1px solid rgba(255,255,255,.10);
        font-size:15px;
        font-weight:850;
        display:flex;
        align-items:center;
        gap:10px;
        }

        .card-body{ padding:18px; }

        /* ===== Forms ===== */
        .form-row{
        display:flex;
        gap:14px;
        margin-bottom:16px;
        }

        @media (max-width:768px){
        .form-row{ flex-direction:column; }
        }

        .form-group{ flex:1; }

        .form-label{
        display:block;
        margin-bottom:8px;
        font-weight:850;
        color:var(--text-light);
        font-size:12.5px;
        }

        .form-control{
        width:100%;
        padding:11px 12px;
        border:1px solid rgba(255,255,255,.12);
        border-radius:16px;
        font-size:13px;
        color:var(--text);
        background:rgba(255,255,255,.06);
        transition:border-color .18s ease, box-shadow .18s ease, background .18s ease;
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        box-shadow: inset 0 1px 0 rgba(255,255,255,.12);
        }

        .form-control::placeholder{ color:rgba(255,255,255,.30); }

        .form-control:focus{
        border-color:rgba(110,168,255,.45);
        outline:none;
        box-shadow:0 0 0 3px rgba(110,168,255,.16);
        background:rgba(255,255,255,.08);
        }

        /* ===== Table ===== */
        .table-container{ overflow-x:auto; }

        .wp-list-table{
        width:100%;
        border-collapse:collapse;
        background:transparent;
        }

        .wp-list-table thead th{
        background:rgba(255,255,255,.04);
        padding:12px;
        text-align:left;
        font-weight:900;
        border-bottom:1px solid rgba(255,255,255,.10);
        color:rgba(255,255,255,.48);
        backdrop-filter: blur(var(--blur)) saturate(170%);
        -webkit-backdrop-filter: blur(var(--blur)) saturate(170%);
        }

        .wp-list-table tbody tr{
        border-bottom:1px solid rgba(255,255,255,.08);
        }

        .wp-list-table tbody tr:hover{
        background:rgba(255,255,255,.05);
        }

        .wp-list-table td{
        padding:12px;
        vertical-align:middle;
        color:var(--text-light);
        }

        /* ===== Badges ===== */
        .badge{
        display:inline-flex;
        align-items:center;
        padding:6px 10px;
        border-radius:var(--pill);
        font-size:12px;
        font-weight:900;
        border:1px solid rgba(255,255,255,.12);
        background:rgba(255,255,255,.06);
        color:var(--text);
        }

        .badge-danger{
        background: rgba(255,77,87,.12);
        border-color: rgba(255,77,87,.28);
        }

        .badge-success{
        background: rgba(43,213,118,.12);
        border-color: rgba(43,213,118,.28);
        }

        /* ===== IP Address ===== */
        .ip-address{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        background: rgba(255,255,255,.06);
        padding: 8px 12px;
        border-radius: 16px;
        border: 1px solid rgba(255,255,255,.12);
        font-size: 12.5px;
        color: var(--text);
        }

        /* ===== Reason Box ===== */
        .reason-box{
        background: rgba(255,255,255,.06);
        padding: 10px 12px;
        border-radius: 16px;
        border: 1px solid rgba(255,255,255,.10);
        font-size: 12.5px;
        margin-top: 6px;
        position: relative;
        }

        .reason-box::before{
        content:"";
        position:absolute;
        left:12px;
        top:10px;
        bottom:10px;
        width:3px;
        border-radius:999px;
        background: linear-gradient(180deg, rgba(255,77,87,.9), rgba(255,77,87,.35));
        }

        /* ===== Alert Box ===== */
        .alert{
        padding:16px;
        margin-bottom:18px;
        border-radius:var(--radius2);
        border:1px solid rgba(255,255,255,.10);
        }

        .alert-warning{
        background: rgba(255,204,71,.10);
        border-color: rgba(255,204,71,.22);
        color: rgba(255,255,255,.85);
        }

        /* ===== Action Buttons ===== */
        .action-buttons{
        display:flex;
        gap:8px;
        }

        .btn-sm{
        padding:8px 12px;
        font-size:12px;
        }

        /* ===== Responsive ===== */
        @media (max-width:768px){
        .admin-menu{
            width: 86px;
            right: 10px;
        }
        .admin-menu .menu-text{ display:none; }
        .wrapper{ margin-right: 110px; }
        }

        .no-results{
        text-align:center;
        padding:40px 20px;
        color:var(--text-light);
        }

        .no-results i{
        font-size:46px;
        margin-bottom:14px;
        opacity:.55;
        }

        .help-box{
        border-radius:var(--radius);
        padding:16px;
        margin-top:18px;
        border:1px solid rgba(255,255,255,.10);
        }

        .help-box ul{
        margin:10px 0;
        padding-left:20px;
        }

        .help-box li{ margin-bottom:6px; }

        /* iOS focus ring */
        a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible{
        outline:none;
        box-shadow:0 0 0 3px rgba(110,168,255,.18);
        border-color:rgba(110,168,255,.45);
        }

    </style>
</head>
<body>
    <!-- Top Bar -->
    <div class="top-bar">
        <div class="logo">
            <span>🛑</span>
            <span>License Manager</span>
        </div>
        <div class="user-menu">
            <a href="{{ url_for('dashboard') }}" class="btn btn-primary">
                <span>🏠</span>
                <span>Dashboard</span>
            </a>
            <a href="{{ url_for('logout') }}" class="btn btn-danger">
                <span>🚪</span>
                <span>Logout</span>
            </a>
        </div>
    </div>
    
    <!-- Side Menu -->
    <nav class="admin-menu">
        <a href="{{ url_for('dashboard') }}" class="menu-item">
            <span>📊</span>
            <span class="menu-text">Dashboard</span>
        </a>
        <a href="{{ url_for('customers_page') }}" class="menu-item">
            <span>👥</span>
            <span class="menu-text">Customers</span>
        </a>
        <a href="{{ url_for('products') }}" class="menu-item">
            <span>📦</span>
            <span class="menu-text">Products</span>
        </a>
        <a href="{{ url_for('blacklist_page') }}" class="menu-item active">
            <span>🛑</span>
            <span class="menu-text">Blacklist</span>
        </a>
    </nav>
    
    <!-- Main Content -->
    <div class="wrapper">
        <h1 class="page-title">IP Blacklist Management</h1>
        
        <!-- Add to Blacklist Form -->
        <div class="card">
            <div class="card-header">
                <span>➕</span>
                <span>Add IP to Blacklist</span>
            </div>
            <div class="card-body">
                <form method="post" action="{{ url_for('add_to_blacklist') }}">
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">IP Address</label>
                            <input type="text" name="ip" class="form-control" 
                                   placeholder="e.g., 192.168.1.1 or 192.168.1.*" 
                                   required
                                   pattern="^(\d{1,3}\.){3}(\d{1,3}|\*)$"
                                   title="Please enter a valid IP address (e.g., 192.168.1.1)">
                        </div>
                        <div class="form-group">
                            <label class="form-label">Block Reason</label>
                            <input type="text" name="reason" class="form-control" 
                                   placeholder="Reason for blocking (optional)">
                        </div>
                    </div>
                    <button type="submit" class="btn btn-danger">
                        <span>🚫</span>
                        <span>Add to Blacklist</span>
                    </button>
                </form>
            </div>
        </div>
        
        <!-- Blacklist Table -->
        <div class="card">
            <div class="card-header">
                <span>📋</span>
                <span>Blacklisted IP Addresses ({{ blacklist|length }})</span>
            </div>
            <div class="card-body">
                {% if blacklist %}
                <div class="alert alert-warning">
                    <strong>Note:</strong> IP addresses in this list cannot access the API system.
                </div>
                
                <div class="table-container">
                    <table class="wp-list-table">
                        <thead>
                            <tr>
                                <th>IP Address</th>
                                <th>Block Reason</th>
                                <th>Date Added</th>
                                <th>Added By</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for item in blacklist %}
                            {% if item is mapping %}
                            <tr>
                                <td>
                                    <span class="ip-address">{{ item.ip }}</span>
                                </td>
                                <td>
                                    {% if item.reason %}
                                    <div class="reason-box">{{ item.reason }}</div>
                                    {% else %}
                                    <span style="color: var(--text-light);">No reason provided</span>
                                    {% endif %}
                                </td>
                                <td>
                                    {% if item.added %}
                                    {{ (item.added | int) | datetime }}
                                    {% else %}
                                    -
                                    {% endif %}
                                </td>
                                <td>
                                    {{ item.added_by or 'System' }}
                                </td>
                                <td>
                                    <div class="action-buttons">
                                        <a href="{{ url_for('remove_from_blacklist', ip=item.ip) }}" 
                                           class="btn btn-success btn-sm"
                                           onclick="return confirm('Are you sure you want to remove this IP from blacklist?')">
                                            <span>✅</span>
                                            <span>Unblock</span>
                                        </a>
                                    </div>
                                </td>
                            </tr>
                            {% else %}
                            <!-- Legacy format compatibility -->
                            <tr>
                                <td>
                                    <span class="ip-address">{{ item }}</span>
                                </td>
                                <td>
                                    <span style="color: var(--text-light);">No reason provided</span>
                                </td>
                                <td>-</td>
                                <td>System</td>
                                <td>
                                    <div class="action-buttons">
                                        <a href="{{ url_for('remove_from_blacklist', ip=item) }}" 
                                           class="btn btn-success btn-sm"
                                           onclick="return confirm('Are you sure you want to remove this IP from blacklist?')">
                                            <span>✅</span>
                                            <span>Unblock</span>
                                        </a>
                                    </div>
                                </td>
                            </tr>
                            {% endif %}
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="no-results">
                    <span>🛡️</span>
                    <h3>Blacklist is Empty</h3>
                    <p>No IP addresses have been blocked yet.</p>
                </div>
                {% endif %}
            </div>
        </div>
        
        <!-- Help Section -->
        <div class="card">
            <div class="card-header">
                <span>❓</span>
                <span>Usage Guide</span>
            </div>
            <div class="card-body">
                <div class="help-box">
                    <strong>Important Notes:</strong>
                    <ul>
                        <li>Blocked IP addresses cannot access the API system</li>
                        <li>You can use wildcard (*) to block IP ranges</li>
                        <li>Example: 192.168.1.* blocks all IPs from 192.168.1.0 to 192.168.1.255</li>
                        <li>Blocked IP attempts are logged in the system</li>
                        <li>Use this feature carefully to avoid blocking legitimate users</li>
                        <li>IP format: XXX.XXX.XXX.XXX or XXX.XXX.XXX.*</li>
                    </ul>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

BULK_RESULT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bulk Generation Result</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
        }
        
        body {
            background: #f0f0f1;
            color: #1d2327;
            line-height: 1.5;
            padding: 20px;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .result-container {
            width: 100%;
            max-width: 800px;
        }
        
        .card {
            background: white;
            border: 1px solid #c3c4c7;
            border-radius: 3px;
            box-shadow: 0 1px 1px rgba(0,0,0,0.04);
            overflow: hidden;
        }
        
        .card-header {
            padding: 20px;
            border-bottom: 1px solid #c3c4c7;
            background: #2271b1;
            color: white;
            font-size: 20px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .card-body {
            padding: 30px;
        }
        
        .success-message {
            background: #d1f7c4;
            border-left: 4px solid #00a32a;
            padding: 15px;
            margin-bottom: 20px;
            border-radius: 3px;
            color: #0c3b1e;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .license-list {
            background: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 3px;
            padding: 15px;
            margin-bottom: 20px;
        }
        
        textarea {
            width: 100%;
            padding: 12px;
            border: 1px solid #c3c4c7;
            border-radius: 3px;
            font-family: 'SF Mono', Monaco, Consolas, monospace;
            font-size: 14px;
            min-height: 200px;
            resize: vertical;
            margin-bottom: 20px;
        }
        
        .btn {
            padding: 10px 20px;
            border-radius: 3px;
            border: none;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s;
        }
        
        .btn-primary {
            background: #2271b1;
            color: white;
        }
        
        .btn-primary:hover {
            background: #135e96;
        }
        
        .btn-secondary {
            background: #f0f0f1;
            color: #1d2327;
        }
        
        .btn-secondary:hover {
            background: #e0e0e0;
        }
        
        .button-group {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }
        
        .stats {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 3px;
        }
        
        .stat-item {
            flex: 1;
            text-align: center;
        }
        
        .stat-number {
            font-size: 24px;
            font-weight: 600;
            color: #2271b1;
            margin-bottom: 5px;
        }
        
        .stat-label {
            color: #50575e;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
    </style>
</head>
<body>
    <div class="result-container">
        <div class="card">
            <div class="card-header">
                <span>✅</span>
                <span>Bulk Generation Complete</span>
            </div>
            <div class="card-body">
                <div class="success-message">
                    <span>🎉</span>
                    <span>Successfully generated {{ keys|length }} license keys!</span>
                </div>
                
                <div class="stats">
                    <div class="stat-item">
                        <div class="stat-number">{{ keys|length }}</div>
                        <div class="stat-label">Keys Generated</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number">{{ keys[0][:8] }}...</div>
                        <div class="stat-label">Sample Key</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-number">{{ datetime.now().strftime('%H:%M') }}</div>
                        <div class="stat-label">Generated At</div>
                    </div>
                </div>
                
                <div class="license-list">
                    <strong>Copy these keys:</strong>
                    <textarea readonly>{% for key in keys %}{{ key }}
{% endfor %}</textarea>
                </div>
                
                <div class="button-group">
                    <button onclick="copyToClipboard()" class="btn btn-primary">
                        <span>📋</span>
                        <span>Copy All Keys</span>
                    </button>
                    <a href="{{ url_for('dashboard') }}" class="btn btn-secondary">
                        <span>←</span>
                        <span>Back to Dashboard</span>
                    </a>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        function copyToClipboard() {
            const textarea = document.querySelector('textarea');
            textarea.select();
            document.execCommand('copy');
            
            const button = document.querySelector('.btn-primary');
            const originalText = button.innerHTML;
            button.innerHTML = '<span>✅</span><span>Copied!</span>';
            button.style.background = '#00a32a';
            
            setTimeout(() => {
                button.innerHTML = originalText;
                button.style.background = '';
            }, 2000);
        }
    </script>
</body>
</html>
"""

# ============ Discord Bot Integration (Optional) ============

def run_discord_bot():
    try:
        import discord
        from discord.ext import commands
        
        intents = discord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix='!', intents=intents)
        
        @bot.event
        async def on_ready():
            print(f'✅ Discord bot connected as {bot.user}')
        
        @bot.command(name='check')
        async def check_license(ctx, license_key: str):
            """Check license status"""
            db = load_db()
            if license_key in db["licenses"]:
                lic = db["licenses"][license_key]
                embed = discord.Embed(title="✅ License Valid", color=0x00ff00)
                embed.add_field(name="Customer", value=lic["customer"])
                embed.add_field(name="Product", value=lic["product"])
                embed.add_field(name="Status", value="Active" if (lic["expire"] == 0 or time.time() < lic["expire"]) else "Expired")
                await ctx.send(embed=embed)
            else:
                await ctx.send("❌ License not found")
        
        @bot.command(name='customer')
        async def customer_info(ctx, customer_id: str):
            """Get customer licenses"""
            customers = load_customers()
            db = load_db()
            
            if customer_id not in customers:
                await ctx.send("❌ Customer not found")
                return
            
            customer_licenses = []
            for key, lic in db["licenses"].items():
                if lic.get("customer_id") == customer_id:
                    customer_licenses.append(f"`{key}` - {lic['product']}")
            
            embed = discord.Embed(title=f"Customer: {customers[customer_id]['name']}", color=0x0099ff)
            embed.add_field(name="Email", value=customers[customer_id].get("email", "N/A"))
            embed.add_field(name="Discord ID", value=customers[customer_id].get("discord_id", "N/A"))
            embed.add_field(name="Licenses", value=f"**{len(customer_licenses)}** licenses" if customer_licenses else "No licenses", inline=False)
            
            if customer_licenses:
                embed.add_field(name="License List", value="\n".join(customer_licenses[:10]), inline=False)
            
            await ctx.send(embed=embed)
        
        # Add your Discord bot token here
        bot_token = ""  # Set your Discord bot token
        if bot_token:
            bot.run(bot_token)
    except ImportError:
        print(" discord.py not installed. Discord bot disabled.")
    except Exception as e:
        print(f"  Discord bot error: {e}")

# ============ Main Execution ============

if __name__ == "__main__":
    # Start Discord bot in separate thread if token is provided
    if False:  # Set to True and add bot token to enable Discord bot
        bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
        bot_thread.start()
    
    print("=" * 50)
    print(" License Manager v2.0")
    print("=" * 50)
    print(" Web Interface: http://localhost:5000")
    print(" Admin Password:", ADMIN_PASS)
    print(f"Secret Key: {app.secret_key}")
    print("=" * 50)
    
    app.run(host="0.0.0.0", port=5000, debug=False)
