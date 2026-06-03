import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import os


# ─────────────────────────────────────────────
# PLAID CONFIG
# ─────────────────────────────────────────────
PLAID_ENV         = st.secrets.get("PLAID_ENV", "sandbox")
PLAID_CLIENT_ID   = st.secrets.get("PLAID_CLIENT_ID", "")
PLAID_SECRET      = (
    st.secrets.get("PLAID_SECRET_SANDBOX")
    if PLAID_ENV == "sandbox"
    else st.secrets.get("PLAID_SECRET_PRODUCTION")
)

PLAID_BASE = {
    "sandbox":    "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}.get(PLAID_ENV, "https://sandbox.plaid.com")

TOKEN_FILE = "plaid_token.json"   # stored in repo root, ignored by git

# ─────────────────────────────────────────────
# TOKEN PERSISTENCE
# ─────────────────────────────────────────────
def load_token():
    """Load stored access token from file."""
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE) as f:
                data = json.load(f)
                return data.get("access_token"), data.get("item_id"), data.get("institution")
    except Exception:
        pass
    return None, None, None

def save_token(access_token, item_id, institution_name):
    """Persist access token to file."""
    try:
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "access_token":    access_token,
                "item_id":         item_id,
                "institution":     institution_name,
            }, f)
        return True
    except Exception:
        return False

def delete_token():
    """Remove stored token — disconnects the account."""
    try:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        return True
    except Exception:
        return False

# ─────────────────────────────────────────────
# PLAID API HELPERS
# ─────────────────────────────────────────────
def plaid_post(endpoint, body):
    """Make an authenticated POST to the Plaid API. Returns full response dict."""
    try:
        url = f"{PLAID_BASE}{endpoint}"
        payload = {
            "client_id": PLAID_CLIENT_ID,
            "secret":    PLAID_SECRET,
            **body,
        }
        resp = requests.post(url, json=payload, timeout=15)
        return resp.status_code, resp.json()
    except Exception as e:
        return 0, {"error": str(e)}

def create_link_token():
    """Create a Plaid Link token with Hosted Link enabled.
    Returns (link_token, hosted_link_url, error_string).
    Hosted Link lets us redirect the browser to Plaid's own page,
    bypassing Streamlit's iframe CSP restrictions entirely.
    """
    status, result = plaid_post("/link/token/create", {
        "user":         {"client_user_id": "voskuil-fp-user"},
        "client_name":  "Voskuil FP",
        "products":     ["transactions"],
        "country_codes":["US"],
        "language":     "en",
        "hosted_link":  {},   # enables hosted_link_url in response
        "redirect_uri": "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app/connect",
    })
    if result.get("link_token"):
        return (
            result["link_token"],
            result.get("hosted_link_url"),
            None,
        )
    error = (
        result.get("error_message")
        or result.get("display_message")
        or result.get("error")
        or f"HTTP {status}: {result}"
    )
    return None, None, error

def exchange_public_token(public_token):
    """Exchange a public token for a permanent access token."""
    status, result = plaid_post("/item/public_token/exchange", {
        "public_token": public_token,
    })
    if result.get("access_token"):
        return result["access_token"], result.get("item_id"), None
    error = (
        result.get("error_message")
        or result.get("error")
        or f"HTTP {status}: {result}"
    )
    return None, None, error

def get_institution_name(item_id):
    """Get the institution name for a connected Item."""
    try:
        _, item_result = plaid_post("/item/get", {"access_token": st.session_state.get("plaid_token")})
        inst_id = item_result.get("item", {}).get("institution_id")
        if inst_id:
            _, inst_result = plaid_post("/institutions/get_by_id", {
                "institution_id": inst_id,
                "country_codes":  ["US"],
            })
            return inst_result.get("institution", {}).get("name", "Unknown Institution")
    except Exception:
        pass
    return "Unknown Institution"

def test_connection(access_token):
    """Verify a stored token still works."""
    _, result = plaid_post("/accounts/get", {"access_token": access_token})
    if "accounts" in result:
        return True, result["accounts"]
    return False, []

# ─────────────────────────────────────────────
# LOAD EXISTING TOKEN INTO SESSION STATE
# ─────────────────────────────────────────────
if "plaid_token" not in st.session_state:
    token, item_id, institution = load_token()
    if token:
        st.session_state.plaid_token       = token
        st.session_state.plaid_item_id     = item_id
        st.session_state.plaid_institution = institution

# ─────────────────────────────────────────────
# PAGE UI
# ─────────────────────────────────────────────
st.title("🔗 Connect Your Account")
st.caption("Connect your Morgan Stanley account via Plaid to automatically pull balance and transaction data.")

env_badge = "🟡 Sandbox (test data)" if PLAID_ENV == "sandbox" else "🟢 Production (live data)"
st.info(f"**Plaid environment:** {env_badge} · To switch, change `PLAID_ENV` in Streamlit secrets.")

st.divider()

# ── Already connected ──────────────────────────────────────────────────────
if st.session_state.get("plaid_token"):
    token       = st.session_state.plaid_token
    institution = st.session_state.get("plaid_institution", "Your account")

    st.success(f"✅ **{institution}** is connected.")

    # Verify the token is still valid
    with st.spinner("Verifying connection..."):
        ok, accounts = test_connection(token)

    if ok:
        st.markdown(f"**{len(accounts)} account(s) linked:**")
        for acct in accounts:
            bal = acct.get("balances", {})
            current = bal.get("current")
            name    = acct.get("name", "Account")
            mask    = acct.get("mask", "")
            atype   = acct.get("subtype", acct.get("type", "")).title()
            bal_str = f"${current:,.2f}" if current is not None else "N/A"
            st.markdown(f"- **{name}** (···{mask}) · {atype} · Balance: {bal_str}")

        st.divider()
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("🔌 Disconnect", type="secondary"):
                delete_token()
                for key in ["plaid_token", "plaid_item_id", "plaid_institution"]:
                    st.session_state.pop(key, None)
                st.rerun()
        with col2:
            st.caption("Disconnecting removes the stored token. You can reconnect at any time.")
    else:
        st.warning("⚠️ Stored token is no longer valid — please reconnect.")
        delete_token()
        for key in ["plaid_token", "plaid_item_id", "plaid_institution"]:
            st.session_state.pop(key, None)
        st.rerun()

# ── Not connected — show Link flow ────────────────────────────────────────
else:
    st.markdown("""
    ### What gets connected
    Plaid will connect to your Morgan Stanley account and pull:
    - **Account balances** — total portfolio value, updated daily
    - **Transactions** — YTD dividends, interest, and activity history

    Holdings data (the main table) still comes from your CSV export — Plaid does not
    support Morgan Stanley holdings data.

    ### How to connect
    1. Click **Connect Account** below
    2. Search for "Morgan Stanley" in the Plaid Link popup
    3. Log in with your Morgan Stanley credentials
    4. Approve the connection

    For Sandbox testing, use the test credentials:
    - **Username:** `user_good`
    - **Password:** `pass_good`
    """)

    if not PLAID_CLIENT_ID or not PLAID_SECRET:
        st.error("❌ Plaid credentials not found in Streamlit secrets. Add PLAID_CLIENT_ID, PLAID_SECRET_SANDBOX, and PLAID_SECRET_PRODUCTION.")
        st.stop()

    # ── Diagnostics — shown before spinner so we can see what's happening ──
    with st.expander("🔬 Connection Diagnostics", expanded=True):
        st.caption(f"Environment: `{PLAID_ENV}`")
        st.caption(f"Base URL: `{PLAID_BASE}`")
        st.caption(f"Client ID: `{PLAID_CLIENT_ID[:8]}...`")
        st.caption(f"Secret loaded: `{'yes' if PLAID_SECRET else 'NO — check secret key name'}`")

        if st.button("🧪 Test API Connection"):
            with st.spinner("Testing..."):
                try:
                    test_resp = requests.post(
                        f"{PLAID_BASE}/link/token/create",
                        json={
                            "client_id":    PLAID_CLIENT_ID,
                            "secret":       PLAID_SECRET,
                            "user":         {"client_user_id": "test"},
                            "client_name":  "Voskuil FP",
                            "products":     ["transactions"],
                            "country_codes":["US"],
                            "language":     "en",
                        },
                        timeout=15,
                    )
                    st.write(f"HTTP status: `{test_resp.status_code}`")
                    st.json(test_resp.json())
                except Exception as e:
                    st.error(f"Request failed: {e}")

    # Handle public token returned from Plaid after redirect
    query_params = st.query_params
    if "public_token" in query_params:
        public_token = query_params["public_token"]
        with st.spinner("Exchanging token with Plaid..."):
            access_token, item_id, error = exchange_public_token(public_token)
        if error:
            st.error(f"❌ Token exchange failed: {error}")
        elif access_token:
            institution = get_institution_name(item_id)
            save_token(access_token, item_id, institution)
            st.session_state.plaid_token       = access_token
            st.session_state.plaid_item_id     = item_id
            st.session_state.plaid_institution = institution
            # Clear query params and reload
            st.query_params.clear()
            st.rerun()

    # Create Link token only when user clicks
    if st.button("🔗 Connect Account", type="primary"):
        with st.spinner("Preparing connection..."):
            link_token, hosted_url, error = create_link_token()

        if error or not link_token:
            st.error(f"❌ Could not create Plaid Link token: {error}")
        elif hosted_url:
            # Hosted Link — redirect the browser directly to Plaid's page.
            # This bypasses all iframe/CSP issues since Plaid hosts the flow.
            st.session_state.plaid_link_token = link_token
            st.markdown(
                f"""
                <meta http-equiv="refresh" content="1;url={hosted_url}">
                <p>✅ Redirecting to Plaid... <a href="{hosted_url}">Click here if not redirected automatically.</a></p>
                """,
                unsafe_allow_html=True,
            )
        else:
            # Fallback: Hosted Link URL not returned — show manual link
            st.session_state.plaid_link_token = link_token
            st.warning("Hosted Link URL not returned. Check that your Plaid account supports Hosted Link.")
            st.write(f"Link token created: `{link_token[:20]}...`")

st.divider()
st.markdown("""
**Privacy note:** Your Plaid access token is stored locally in `plaid_token.json` in your
app's file system on Streamlit Cloud. It is never logged or transmitted anywhere other than
to Plaid's API. You can disconnect at any time using the button above.
""")
