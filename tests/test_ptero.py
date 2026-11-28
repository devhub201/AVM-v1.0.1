import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lc_ptero as p

def test_token():
    t = "Ztn0ywEJs4bn2m3RmCxu7M7maJXh3G0AOOep9RTWAmM"
    assert p.parse_token(t) == t
    assert p.parse_token(f"bash <(curl -fsSL https://labs-api.serververs.com/api/v0.3/provision/install.sh) --token {t}") == t
    assert p.parse_token("short") is None
    assert p.parse_token("a;rm -rf /;aaaaaaaaaaaaaaaaaaaaaaaa") is None

def test_ips():
    assert p.public_ipv4s("local 10.0.0.5 and 192.168.1.1 and 203.0.113.9? no") == []  # 203.0.113.0/24 is documentation range
    assert p.public_ipv4s("assigned 8.8.4.4 via 10.1.1.1, again 8.8.4.4") == ["8.8.4.4"]

def test_answers():
    ok, err = p.validate_answers("a@b.co", "", "", "", "yes"); assert err is None and ok["user"] == "admin" and ok["generated"]
    assert p.password_problem(ok["password"]) is None
    assert p.validate_answers("bad", "admin", "", "", "yes")[1]
    assert p.validate_answers("a@b.co", "Ad min", "", "", "yes")[1]
    assert p.validate_answers("a@b.co", "admin", "weak", "", "yes")[1]
    assert p.validate_answers("a@b.co", "admin", "Strong123", "../x", "yes")[1]
    assert p.validate_answers("a@b.co", "admin", "Strong123", "UTC", "maybe")[1]
    assert p.validate_answers("a@b.co", "admin", "Strong123", "UTC", "no")[0]["wings"] is False

def test_state_and_progress():
    assert p.parse_state("RUN|3|8|Database and cache") == {"kind": "run", "idx": 3, "total": 8, "name": "Database and cache"}
    f = p.parse_state("FAIL|4|Panel files|curl a|b (exit 22)"); assert f["kind"] == "fail" and f["msg"] == "curl a|b (exit 22)"
    assert p.parse_state("DONE")["kind"] == "done" and p.parse_state("junk")["kind"] == "none"
    assert "100%" in p.progress_text({"kind": "done"}) and "0%" in p.progress_text({"kind": "none"})
    assert "❌" in p.progress_text(f)

def test_redact():
    assert p.redact("token abc123 end", ["abc123"]) == "token *** end"

def test_env_quoting():
    env = p.build_env({k: "x y'z;$(id)" for k in ["PANEL_URL","PUBLIC_IP","ADMIN_EMAIL","ADMIN_USER","ADMIN_PASS","TZ_NAME","WANT_WINGS","API_PORT","SFTP_PORT","GAME_PORTS","NODE_NAME","BRAND"]})
    out = subprocess.run(["bash", "-c", env + 'printf %s "$PANEL_URL"'], capture_output=True, text=True).stdout
    assert out == "x y'z;$(id)"

def test_script_syntax():
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(p.INSTALL_SCRIPT)
    r = subprocess.run(["bash", "-n", fh.name], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for step in ("s_prepare","s_web","s_db","s_files","s_setup","s_docker","s_wings","s_node"):
        assert f"{step}()" in p.INSTALL_SCRIPT

def test_brand():
    assert p.clean_brand("") == "Legacy Cloud Free VPS"
    assert p.clean_brand("My <b>Host</b>'; DROP") == "My bHostb DROP"
    assert len(p.clean_brand("x" * 99)) == 40
    assert p.clean_brand("Legacy Cloud Free VPS") == "Legacy Cloud Free VPS"
    assert "BRAND" in p.build_env({k: "v" for k in ["PANEL_URL","PUBLIC_IP","ADMIN_EMAIL","ADMIN_USER","ADMIN_PASS","TZ_NAME","WANT_WINGS","API_PORT","SFTP_PORT","GAME_PORTS","NODE_NAME","BRAND"]})
