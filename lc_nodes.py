"""Legacy Cloud multi-node layer for AVM.

Add machines running LXC / Docker / KVM and let the bot deploy to and manage them over SSH.
Connection can be a normal SSH address, a Pinggy TCP tunnel (tcp://host:port) or an SSHX link
(SSHX is an interactive web terminal, so it is kept as a staff "open terminal" link only).

How it plugs in (no rewrite of the 6500-line bot):
  * avm_bot talks to `lxc` through asyncio.create_subprocess_exec. We swap the module's `asyncio`
    for a thin proxy; when a command targets a container that lives on a remote node, the proxy
    sends it over SSH (and translates it for Docker / KVM nodes) instead of running it locally.
  * deploy_vps_container is wrapped: pick a node, build there, remember the node, log the build.
  * scan_container / container_state are wrapped so Mining Guard works on remote nodes too.

Loaded from the bottom of avm_bot.py:  lc_nodes.setup(sys.modules[__name__])
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import shlex
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

NODE_TYPES = ("lxc", "docker", "kvm")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,23}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,251}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

CUR_NODE: contextvars.ContextVar = contextvars.ContextVar("lc_node", default=None)    # node dict or None = local
STEPS: contextvars.ContextVar = contextvars.ContextVar("lc_steps", default=None)      # list of build steps
ON_CREATED: List[Any] = []                                                            # async hooks(info)

LOCAL = {"name": "local", "type": "lxc", "local": True}


class NodeError(Exception):
    """A problem with a clear, user-safe message."""


class NodeUnsupported(NodeError):
    pass


# ----------------------------------------------------------------------------- pure helpers
def parse_target(text: str) -> Tuple[Optional[str], str, Optional[int]]:
    """'tcp://abc.a.pinggy.link:41234' | 'root@1.2.3.4:2222' | 'host' -> (user, host, port)."""
    t = (text or "").strip()
    t = re.sub(r"^(tcp|ssh)://", "", t, flags=re.I).rstrip("/")
    user = None
    if "@" in t:
        user, t = t.split("@", 1)
    port = None
    if t.count(":") == 1:
        t, p = t.split(":")
        if not p.isdigit() or not (0 < int(p) < 65536):
            raise NodeError(f"`{p}` is not a valid port.")
        port = int(p)
    if not HOST_RE.match(t):
        raise NodeError("That does not look like a valid host name or IPv4 address.")
    if user is not None and not USER_RE.match(user):
        raise NodeError("That does not look like a valid SSH username.")
    return user, t, port


def docker_image(os_version: str) -> str:
    v = (os_version or "").lower()
    table = {
        "images:debian/10": "debian:10", "images:debian/11": "debian:11", "images:debian/12": "debian:12",
        "images:debian/13": "debian:13", "images:rockylinux/9": "rockylinux:9",
        "images:almalinux/9": "almalinux:9", "images:fedora/39": "fedora:39",
    }
    if v in table:
        return table[v]
    if v.startswith("ubuntu:"):
        return v
    raise NodeError(f"No Docker image mapped for `{os_version}`.")


def translate(node: Dict[str, Any], args: List[str]) -> List[str]:
    """Translate an `lxc ...` argv for the node's runtime. Raises NodeUnsupported when there is no safe equivalent."""
    t = node.get("type", "lxc")
    a = [str(x) for x in args]
    if t == "lxc":
        if node.get("pool") and "-s" in a:
            i = a.index("-s")
            if i + 1 < len(a):
                a[i + 1] = node["pool"]
        return a
    verb = a[1] if len(a) > 1 else ""
    rest = a[2:]
    force = "--force" in rest or "-f" in rest
    names = [x for x in rest if not x.startswith("-")]
    name = names[0] if names else ""
    if verb == "list":
        return ["docker", "ps", "-a"] if t == "docker" else ["virsh", "list", "--all"]
    if verb not in ("start", "stop", "restart", "delete", "info", "exec"):
        raise NodeUnsupported(f"`{verb}` is not supported on {t} nodes yet.")
    if not SAFE_NAME.match(name or ""):
        raise NodeUnsupported("Unsafe container name.")
    if t == "docker":
        if verb == "start":
            return ["docker", "start", name]
        if verb == "stop":
            return ["docker", "kill" if force else "stop", name]
        if verb == "restart":
            return ["docker", "restart", name]
        if verb == "delete":
            return ["docker", "rm", "-f", name]
        if verb == "info":
            return ["sh", "-c", "echo \"Status: $(docker inspect -f '{{.State.Status}}' %s 2>/dev/null | sed 's/exited/stopped/' || echo unknown)\"" % name]
        if "--" in a:
            return ["docker", "exec", name] + a[a.index("--") + 1:]
        raise NodeUnsupported("Malformed exec.")
    if t == "kvm":
        if verb == "start":
            return ["virsh", "start", name]
        if verb == "stop":
            return ["virsh", "destroy" if force else "shutdown", name]
        if verb == "restart":
            return ["virsh", "reboot", name]
        if verb == "delete":
            return ["sh", "-c", f"virsh destroy {name} 2>/dev/null; virsh undefine {name} --remove-all-storage"]
        if verb == "info":
            return ["sh", "-c", f"echo \"Status: $(virsh domstate {name} 2>/dev/null | sed 's/shut off/stopped/' || echo unknown)\""]
        raise NodeUnsupported("Running commands inside KVM guests needs guest-agent access, which is not available yet.")
    raise NodeUnsupported(f"Unknown node type `{t}`.")


STATS_SCRIPT = r"""
echo "LOAD $(cut -d' ' -f1 /proc/loadavg)"
echo "CORES $(nproc 2>/dev/null || echo 1)"
awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{print "MEM",t*1024,a*1024}' /proc/meminfo
df -B1 / 2>/dev/null | awk 'NR==2{print "DISK",$2,$3}'
awk '{print "UP",int($1)}' /proc/uptime
if command -v lxc >/dev/null 2>&1; then echo "LXC $(lxc list -c s -f csv 2>/dev/null | grep -ci running) $(lxc list -c s -f csv 2>/dev/null | wc -l)"; fi
if command -v docker >/dev/null 2>&1; then echo "DOCKER $(docker ps -q 2>/dev/null | wc -l) $(docker ps -aq 2>/dev/null | wc -l)"; fi
if command -v virsh >/dev/null 2>&1; then echo "KVM $(virsh list --name 2>/dev/null | grep -c .) $(virsh list --all --name 2>/dev/null | grep -c .)"; fi
echo "OK"
"""


def parse_stats(text: str) -> Dict[str, Any]:
    """Pure: parse STATS_SCRIPT output."""
    out: Dict[str, Any] = {"guests": {}}
    for line in (text or "").splitlines():
        p = line.split()
        if not p:
            continue
        try:
            if p[0] == "LOAD":
                out["load"] = float(p[1])
            elif p[0] == "CORES":
                out["cores"] = max(1, int(p[1]))
            elif p[0] == "MEM":
                out["mem_total"], out["mem_free"] = int(p[1]), int(p[2])
            elif p[0] == "DISK":
                out["disk_total"], out["disk_used"] = int(p[1]), int(p[2])
            elif p[0] == "UP":
                out["uptime"] = int(p[1])
            elif p[0] in ("LXC", "DOCKER", "KVM"):
                out["guests"][p[0].lower()] = (int(p[1]), int(p[2]))
            elif p[0] == "OK":
                out["ok"] = True
        except (ValueError, IndexError):
            continue
    if "load" in out:
        out["cpu_pct"] = min(100.0, out["load"] / out.get("cores", 1) * 100)
    if out.get("mem_total"):
        out["mem_pct"] = (out["mem_total"] - out.get("mem_free", 0)) / out["mem_total"] * 100
    if out.get("disk_total"):
        out["disk_pct"] = out.get("disk_used", 0) / out["disk_total"] * 100
    return out


def join_script(pubkey: str, pinggy: bool, user: str = "root") -> str:
    base = (f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && grep -qF '{pubkey}' ~/.ssh/authorized_keys 2>/dev/null || "
            f"echo '{pubkey}' >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; "
            "command -v sshd >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq openssh-server) >/dev/null 2>&1; "
            "(systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || service ssh start 2>/dev/null); ")
    if pinggy:
        base += ("pkill -f 'R0:localhost:22' 2>/dev/null; "
                 "nohup ssh -p 443 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 "
                 "-o ServerAliveCountMax=3 -R0:localhost:22 tcp@a.pinggy.io >/tmp/pinggy.log 2>&1 </dev/null & "
                 "sleep 12; grep -o 'tcp://[^ ]*' /tmp/pinggy.log | head -1")
    else:
        base += "echo READY"
    return base


# ----------------------------------------------------------------------------- setup
def setup(m):
    import discord
    from discord.ext import commands, tasks

    bot = m.bot
    real_asyncio = asyncio
    data_dir = os.path.join(m.BASE_DIR, "lc_data")
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    KEY = os.path.join(data_dir, "id_ed25519")
    KNOWN = os.path.join(data_dir, "known_hosts")
    NODES: Dict[str, Dict[str, Any]] = {}
    HEALTH: Dict[str, Dict[str, Any]] = {}
    state = {"tick": 0, "host_alerted": {}}

    # ---------------------------------------------------------- storage
    def load_nodes():
        NODES.clear()
        raw = m.get_setting("nodes_json", "")
        if not raw:
            return
        try:
            for name, n in json.loads(raw).items():
                NODES[name] = n
        except Exception as exc:
            m.logger.error(f"nodes_json is invalid, ignoring stored nodes: {exc}")

    def save_nodes():
        m.set_setting("nodes_json", json.dumps(NODES))

    load_nodes()

    def local_enabled() -> bool:
        return m.get_setting("local_deploy", "true").lower() == "true" and bool(shutil.which("lxc"))

    def get_node(name: str) -> Optional[Dict[str, Any]]:
        n = (name or "").lower()
        if n == "local":
            return LOCAL
        return NODES.get(n)

    def vps_count(node_name: str) -> int:
        c = 0
        for lst in m.vps_data.values():
            for v in lst:
                if (v.get("node") or "local") == node_name:
                    c += 1
        return c

    # ---------------------------------------------------------- ssh
    def have_key() -> bool:
        return os.path.exists(KEY) and os.path.exists(KEY + ".pub")

    async def ensure_key() -> str:
        if not have_key():
            if not shutil.which("ssh-keygen"):
                raise NodeError("`ssh-keygen` is missing on this server. Install it: `apt install -y openssh-client`.")
            proc = await real_asyncio.create_subprocess_exec(
                "ssh-keygen", "-t", "ed25519", "-N", "", "-C", "legacycloud-bot", "-f", KEY,
                stdout=real_asyncio.subprocess.PIPE, stderr=real_asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise NodeError(f"Could not create the SSH key: {err.decode()[:200]}")
            os.chmod(KEY, 0o600)
        with open(KEY + ".pub", "r", encoding="utf-8") as fh:
            return fh.read().strip()

    def ssh_argv(node: Dict[str, Any], remote_cmd: str) -> Tuple[List[str], Optional[Dict[str, str]]]:
        if not shutil.which("ssh"):
            raise NodeError("`ssh` is missing on this server. Install it: `apt install -y openssh-client`.")
        argv: List[str] = []
        env = None
        use_pw = node.get("auth") == "password"
        if use_pw:
            if not shutil.which("sshpass"):
                raise NodeError("Password login needs `sshpass`. Install it (`apt install -y sshpass`) or use key login.")
            argv += ["sshpass", "-e"]
            env = dict(os.environ, SSHPASS=str(node.get("password", "")))
        argv += ["ssh", "-p", str(node.get("port", 22)),
                 "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN}",
                 "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                 "-o", "LogLevel=ERROR"]
        if not use_pw:
            argv += ["-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", KEY]
        argv += [f"{node.get('user', 'root')}@{node['host']}", remote_cmd]
        return argv, env

    async def node_run(node: Dict[str, Any], remote_cmd: str, timeout: float = 60) -> Tuple[int, str, str]:
        """Run a shell command on a node (or locally for the local node)."""
        if node.get("local"):
            argv, env = ["sh", "-c", remote_cmd], None
        else:
            argv, env = ssh_argv(node, remote_cmd)
        kw = {"env": env} if env else {}
        proc = await real_asyncio.create_subprocess_exec(
            *argv, stdout=real_asyncio.subprocess.PIPE, stderr=real_asyncio.subprocess.PIPE, **kw)
        try:
            out, err = await real_asyncio.wait_for(proc.communicate(), timeout=timeout)
        except real_asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise NodeError(f"Timed out after {int(timeout)}s talking to node `{node['name']}`.")
        return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")

    def explain_ssh_error(err: str) -> str:
        e = (err or "").lower()
        if "permission denied" in e:
            return "SSH login was refused. Add the bot's public key to the node (`.node join` prints the command) or set a password with `.node setpass`."
        if "connection refused" in e:
            return "Connection refused — SSH is not listening on that port (or a Pinggy tunnel expired)."
        if "timed out" in e or "no route" in e or "unreachable" in e:
            return "The node did not answer (offline, wrong address, or a firewall)."
        if "could not resolve" in e or "name or service not known" in e:
            return "The host name could not be resolved."
        if "host key verification failed" in e or "remote host identification" in e:
            return "The node's SSH host key changed. If you rebuilt it, remove its line from `lc_data/known_hosts`."
        return (err or "no output").strip()[:300]

    # ---------------------------------------------------------- asyncio proxy: this is the whole trick
    container_cache: Dict[str, Any] = {"t": 0.0, "map": {}}

    def container_nodes() -> Dict[str, str]:
        now = time.monotonic()
        if now - container_cache["t"] > 2:
            container_cache["map"] = {v["container_name"]: (v.get("node") or "local")
                                      for lst in m.vps_data.values() for v in lst}
            container_cache["t"] = now
        return container_cache["map"]

    def resolve_node(args) -> Optional[Dict[str, Any]]:
        ctx = CUR_NODE.get()
        if ctx is not None:
            return ctx
        cmap = container_nodes()
        for a in args[1:]:
            name = cmap.get(a) if isinstance(a, str) else None
            if name and name != "local":
                node = NODES.get(name)
                if node is None:
                    raise NodeError(f"This VPS lives on node `{name}`, which is no longer registered. Re-add it with `{m.PREFIX}node add`.")
                return node
        return None

    class AsyncioProxy:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, item):
            return getattr(self._real, item)

        async def create_subprocess_exec(self, *args, **kw):
            if args and args[0] == "lxc":
                node = resolve_node(args)
                if node is not None and not node.get("local"):
                    argv = translate(node, list(args))
                    ssh, env = ssh_argv(node, shlex.join(argv))
                    if env:
                        kw["env"] = env
                    return await self._real.create_subprocess_exec(*ssh, **kw)
            return await self._real.create_subprocess_exec(*args, **kw)

    m.asyncio = AsyncioProxy(real_asyncio)

    # ---------------------------------------------------------- wrappers around avm_bot internals
    orig_execute = m.execute_lxc

    async def execute_wrapper(command, timeout=120):
        steps = STEPS.get()
        if steps is None:
            return await orig_execute(command, timeout)
        t0, ok = time.monotonic(), True
        try:
            return await orig_execute(command, timeout)
        except Exception:
            ok = False
            raise
        finally:
            label = command.replace("lxc ", "", 1)
            steps.append((label[:70], time.monotonic() - t0, ok))
    m.execute_lxc = execute_wrapper

    orig_provision = m._provision_container

    async def provision_wrapper(name, os_version, ram, cpu, disk):
        node = CUR_NODE.get()
        if node is None or node.get("local") or node["type"] == "lxc":
            return await orig_provision(name, os_version, ram, cpu, disk)
        if node["type"] == "kvm":
            raise NodeError("KVM nodes can be monitored and controlled, but automatic VPS creation on KVM needs a VM template, which is not available yet. Use an LXC or Docker node for deploys.")
        image = docker_image(os_version)
        steps = STEPS.get()

        async def step(label, cmd, timeout):
            t0 = time.monotonic()
            rc, out, err = await node_run(node, cmd, timeout)
            if steps is not None:
                steps.append((label, time.monotonic() - t0, rc == 0))
            if rc != 0:
                raise NodeError(f"{label} failed on `{node['name']}`: {(err or out).strip()[:300]}")
            return out

        await step(f"docker pull {image}", shlex.join(["docker", "pull", image]), 900)
        await step("docker run (limits applied)", shlex.join(
            ["docker", "run", "-d", "--name", name, "--hostname", name, "--memory", f"{int(ram)}g",
             "--cpus", str(int(cpu)), "--restart", "unless-stopped", "--init", image, "sleep", "infinity"]), 300)
        try:        # best effort: tools the mining scanner needs
            await step("install base tools", shlex.join(["docker", "exec", name, "sh", "-c",
                       "(command -v apt-get >/dev/null && apt-get update -qq && apt-get install -y -qq procps iproute2 curl openssh-client) || true"]), 300)
        except NodeError as exc:
            m.logger.warning(f"Base tools not installed in {name}: {exc}")
        return True            # no LXC-style 'docker-ready' config applies here
    m._provision_container = provision_wrapper

    orig_state = m.container_state

    async def state_wrapper(container):
        n = (container_nodes().get(container) or "local")
        if n != "local" and (NODES.get(n) or {}).get("type") != "lxc":
            return None            # only LXD exposes the JSON state API; scan falls back to a status check
        return await orig_state(container)
    m.container_state = state_wrapper

    orig_scan = m.scan_container

    async def scan_wrapper(uid, vps, punish=True):
        n = vps.get("node") or "local"
        if n != "local" and (NODES.get(n) or {}).get("type") == "kvm":
            return {"container": vps["container_name"], "owner": uid, "severity": None, "findings": [], "cpu": None,
                    "ports": [], "skipped": "KVM guest (no in-guest access)", "processes": 0}
        return await orig_scan(uid, vps, punish)
    m.scan_container = scan_wrapper

    # ---------------------------------------------------------- node health
    async def refresh_node(node: Dict[str, Any]) -> Dict[str, Any]:
        name = node["name"]
        h = HEALTH.setdefault(name, {"up": None, "fails": 0})
        t0 = time.monotonic()
        try:
            rc, out, err = await node_run(node, STATS_SCRIPT, 45)
            stats = parse_stats(out)
            if rc != 0 or not stats.get("ok"):
                raise NodeError(explain_ssh_error(err) if rc != 0 else "The node answered but sent no stats.")
            h.update(up=True, fails=0, stats=stats, error=None, latency=time.monotonic() - t0, checked=time.time())
        except Exception as exc:
            h["fails"] = h.get("fails", 0) + 1
            h.update(error=str(exc) if isinstance(exc, NodeError) else f"{type(exc).__name__}: {exc}",
                     checked=time.time())
            if h["fails"] >= 2 or h.get("up") is None:
                h["up"] = False
        return h

    def pick_node(ram: int, cpu: int, disk: int) -> Optional[Dict[str, Any]]:
        """Return the node to build on (None = this machine). Raises NodeError when nothing can take it."""
        cands: List[Dict[str, Any]] = []
        if local_enabled():
            cap = m._to_int(m.get_setting("local_max_vps", "0"))
            if not cap or vps_count("local") < cap:
                cands.append(LOCAL)
        for n in NODES.values():
            if not n.get("enabled", True) or n["type"] not in ("lxc", "docker"):
                continue
            if HEALTH.get(n["name"], {}).get("up") is False:
                continue
            cap = int(n.get("max_vps") or 0)
            if cap and vps_count(n["name"]) >= cap:
                continue
            cands.append(n)
        if not cands:
            raise NodeError("No node is available to build this VPS (all are full, offline, disabled or cannot deploy). Check `" + m.PREFIX + "node list`.")
        default = m.get_setting("default_node", "")
        for n in cands:
            if n["name"] == default:
                return None if n.get("local") else n
        best = min(cands, key=lambda n: (vps_count(n["name"]) / (int(n.get("max_vps") or 0) or 1000), vps_count(n["name"])))
        return None if best.get("local") else best

    orig_deploy = m.deploy_vps_container

    async def deploy_wrapper(owner_id, ram, cpu, disk, os_version, plan_name="Custom"):
        node = pick_node(ram, cpu, disk)
        steps: List[Tuple[str, float, bool]] = []
        tok_n, tok_s = CUR_NODE.set(node), STEPS.set(steps)
        t0 = time.monotonic()
        try:
            info = await orig_deploy(owner_id, ram, cpu, disk, os_version, plan_name)
        finally:
            CUR_NODE.reset(tok_n)
            STEPS.reset(tok_s)
        info["node"] = node["name"] if node else "local"
        m.save_vps_data()
        container_cache["t"] = 0.0
        report = {"vps": info, "owner": owner_id, "node": info["node"], "node_type": (node or LOCAL)["type"],
                  "seconds": time.monotonic() - t0, "steps": steps,
                  "source": "Admin / manual" if plan_name == "Custom" else f"Free reward • {plan_name}"}
        for hook in list(ON_CREATED):
            try:
                await hook(report)
            except Exception as exc:
                m.logger.error(f"Creation hook failed: {type(exc).__name__}: {exc}")
        return info
    m.deploy_vps_container = deploy_wrapper

    # ---------------------------------------------------------- embeds
    def dot(h) -> str:
        return "🟢" if h.get("up") else ("🔴" if h.get("up") is False else "🟡")

    def node_card(node: Dict[str, Any], public: bool = True) -> Tuple[str, str]:
        h = HEALTH.get(node["name"], {})
        s = h.get("stats") or {}
        title = f"{dot(h)} {node['name']} • {node['type'].upper()}"
        if node.get("local"):
            title += " • this machine"
        lines = []
        if s:
            lines.append(f"🧠 CPU `{m.make_bar(s.get('cpu_pct'), 8)}` {s.get('cpu_pct', 0):.0f}%  (load {s.get('load', 0):.2f}/{s.get('cores', 1)})")
            lines.append(f"🧩 RAM `{m.make_bar(s.get('mem_pct'), 8)}` {s.get('mem_pct', 0):.0f}%  ({m.fmt_bytes(s.get('mem_total', 0) - s.get('mem_free', 0))}/{m.fmt_bytes(s.get('mem_total', 0))})")
            lines.append(f"💽 Disk `{m.make_bar(s.get('disk_pct'), 8)}` {s.get('disk_pct', 0):.0f}%  ({m.fmt_bytes(s.get('disk_used', 0))}/{m.fmt_bytes(s.get('disk_total', 0))})")
            g = s.get("guests", {})
            if g:
                lines.append("📦 " + " • ".join(f"{k}: {a}/{b} running" for k, (a, b) in g.items()))
            lines.append(f"⏱️ Up {m.fmt_duration(s.get('uptime', 0))}" + (f" • ping {h.get('latency', 0) * 1000:.0f} ms" if h.get("latency") else ""))
        elif h.get("error"):
            lines.append(f"❌ {h['error'][:180]}")
        else:
            lines.append("⏳ Waiting for the first health check…")
        mine = vps_count(node["name"])
        cap = int(node.get("max_vps") or 0) if not node.get("local") else m._to_int(m.get_setting("local_max_vps", "0"))
        lines.append(f"🖥️ VPS: **{mine}**" + (f"/{cap}" if cap else ""))
        if not public:
            if not node.get("local"):
                method = "pinggy" if "pinggy" in node["host"] else "ssh"
                lines.append(f"🔗 `{node.get('user', 'root')}@{node['host']}:{node.get('port', 22)}` via {method} • {node.get('auth', 'key')} login")
                if node.get("sshx_url"):
                    lines.append("🖥️ SSHX terminal link saved")
            if not node.get("enabled", True):
                lines.append("⏸️ **Disabled** — no new deploys")
            if node["type"] == "kvm":
                lines.append("ℹ️ KVM: monitor + start/stop only (no auto-create)")
        return title, "\n".join(lines)

    def all_nodes() -> List[Dict[str, Any]]:
        out = [LOCAL] if shutil.which("lxc") else []
        out += list(NODES.values())
        return out

    def board_embed(public: bool = True):
        up = sum(1 for n in all_nodes() if HEALTH.get(n["name"], {}).get("up"))
        total = len(all_nodes())
        embed = m.create_info_embed("Node Status", f"🛰️ **{up}/{total}** nodes online • refreshed <t:{int(time.time())}:R>")
        for n in all_nodes()[:24]:
            t, v = node_card(n, public)
            embed.add_field(name=t, value=v[:1024], inline=False)
        return embed

    async def update_board():
        cid = m._to_int(m.get_setting("node_board_channel", "0"))
        if not cid:
            return
        try:
            ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
            mid = m._to_int(m.get_setting("node_board_msg", "0"))
            embed = board_embed(public=True)
            if mid:
                try:
                    msg = await ch.fetch_message(mid)
                    await msg.edit(embed=embed)
                    return
                except discord.NotFound:
                    pass
            msg = await ch.send(embed=embed)
            m.set_setting("node_board_msg", str(msg.id))
        except Exception as exc:
            m.logger.error(f"Node board update failed: {type(exc).__name__}: {exc}")

    # ---------------------------------------------------------- monitor loop
    async def host_scan(node: Dict[str, Any]):
        rc, out, err = await node_run(node, m._EXEC_SCRIPT, 40)
        procs, ports = m.parse_exec_output(out)
        return procs, m.analyze_process_list(procs), sorted(ports & m.MINING_PORTS)

    @tasks.loop(seconds=60)
    async def node_monitor_loop():
        try:
            state["tick"] += 1
            nodes = all_nodes()
            sem = real_asyncio.Semaphore(4)
            before = {n["name"]: HEALTH.get(n["name"], {}).get("up") for n in nodes}

            async def one(n):
                async with sem:
                    await refresh_node(n)
            await real_asyncio.gather(*(one(n) for n in nodes))
            for n in nodes:
                was, now = before[n["name"]], HEALTH[n["name"]].get("up")
                if was is True and now is False:
                    await m.send_staff_alert(m.create_error_embed("Node Offline",
                        f"🛰️ `{n['name']}` stopped answering.\n{HEALTH[n['name']].get('error', '')[:300]}\nNew deploys skip it until it is back."))
                elif was is False and now is True:
                    await m.send_staff_alert(m.create_success_embed("Node Back Online", f"🛰️ `{n['name']}` is answering again."))
            if m.MINING_GUARD_ENABLED and state["tick"] % 5 == 0:         # host-level anti-mining sweep of remote nodes
                for n in NODES.values():
                    if not n.get("enabled", True) or HEALTH.get(n["name"], {}).get("up") is not True:
                        continue
                    try:
                        _, findings, ports = await host_scan(n)
                    except Exception as exc:
                        m.logger.warning(f"Host scan of {n['name']} failed: {exc}")
                        continue
                    key = ",".join(sorted(str(f["pid"]) for f in findings))
                    if findings and state["host_alerted"].get(n["name"]) != key:
                        state["host_alerted"][n["name"]] = key
                        lines = [f"PID {f['pid']} — {f['reason']}: {f['cmd'][:80]}" for f in findings[:5]]
                        await m.send_staff_alert(m.create_warning_embed("Miner Suspected On Node",
                            f"⛏️ Node `{n['name']}` host shows mining-like processes (report only, nothing was killed):\n```\n" + "\n".join(lines) + "\n```"))
                    elif not findings:
                        state["host_alerted"].pop(n["name"], None)
            await update_board()
        except Exception:
            m.logger.exception("Node monitor loop crashed this cycle")

    started = {"v": False}

    @bot.listen("on_ready")
    async def lc_nodes_ready():
        if started["v"]:
            return
        started["v"] = True
        if not node_monitor_loop.is_running():
            node_monitor_loop.start()
        m.logger.info(f"Nodes ready • registered={len(NODES)} • local={'on' if local_enabled() else 'off'}")

    # ---------------------------------------------------------- commands
    def need_node(ctx_name: str) -> Dict[str, Any]:
        n = get_node(ctx_name)
        if n is None:
            raise NodeError(f"No node named `{ctx_name}`. See `{m.PREFIX}node list`.")
        return n

    async def run_cmd(ctx, coro):
        try:
            return await coro
        except NodeError as exc:
            await ctx.send(embed=m.create_error_embed("Node Problem", str(exc)))
        except Exception as exc:
            m.logger.exception("node command failed")
            await ctx.send(embed=m.create_error_embed("Unexpected Error", f"`{type(exc).__name__}: {exc}`"))
        return None

    @bot.group(name="node", aliases=["nodes"], invoke_without_command=True)
    @m.is_admin()
    async def node_group(ctx):
        p = m.PREFIX
        embed = m.create_info_embed("Nodes", "Run VPS on more machines — LXC, Docker or KVM — over SSH, Pinggy or SSHX.")
        embed.add_field(name="🛰️ Add & connect", value=(
            f"`{p}node join [pinggy]` — command to run on the new machine\n"
            f"`{p}node add <name> <lxc|docker|kvm> <host|tcp://host:port> [port] [user]`\n"
            f"`{p}node setpass <name>` — password login instead of key\n"
            f"`{p}node sshx <name> <url>` / `{p}node terminal <name>` — SSHX web terminal link"), inline=False)
        embed.add_field(name="📊 Manage", value=(
            f"`{p}node list` • `{p}node info <name>` • `{p}node test <name>`\n"
            f"`{p}node enable|disable <name>` • `{p}node limit <name> <max_vps>` • `{p}node pool <name> <lxd_pool>`\n"
            f"`{p}node default <name|off>` — preferred deploy node (otherwise least loaded)\n"
            f"`{p}node board #channel` — live public node board\n"
            f"`{p}node scan <name>` — anti-mining sweep of the node host\n"
            f"`{p}node exec <name> <cmd>` • `{p}node remove <name> [force]` (main admin)"), inline=False)
        embed.add_field(name="ℹ️ Support", value=(
            "**LXC**: full (create, manage, resize, mining guard)\n"
            "**Docker**: create, start/stop/restart/delete, mining guard (disk limit not enforced)\n"
            "**KVM**: monitor + start/stop/restart/delete of existing VMs"), inline=False)
        await ctx.send(embed=embed)

    @node_group.command(name="join")
    @m.is_admin()
    async def node_join(ctx, mode: str = ""):
        async def go():
            pub = await ensure_key()
            pinggy = mode.lower() == "pinggy"
            script = join_script(pub, pinggy)
            if pinggy:
                how = ("Run this **as root on the new machine**. It authorises the bot, starts SSH and opens a Pinggy tunnel, "
                       "then prints `tcp://host:port`. Free tunnels expire after about 60 minutes — use a Pinggy token or a public IP for permanent nodes.\n"
                       f"Then: `{m.PREFIX}node add <name> <lxc|docker|kvm> tcp://host:port`")
            else:
                how = ("Run this **as root on the new machine**. It authorises the bot's key and makes sure SSH is running.\n"
                       f"Then: `{m.PREFIX}node add <name> <lxc|docker|kvm> <ip-or-host> [port]`")
            await ctx.send(embed=m.create_info_embed("Join A Node", how + f"\n```bash\n{script}\n```"))
        await run_cmd(ctx, go())

    @node_group.command(name="add")
    @m.is_main_admin()
    async def node_add(ctx, name: str, ntype: str, target: str, port: int = 0, user: str = ""):
        async def go():
            n = name.lower()
            if not NAME_RE.match(n) or n == "local":
                raise NodeError("Node name: 2-24 chars, lowercase letters, numbers, `-` or `_` (and not `local`).")
            if n in NODES:
                raise NodeError(f"A node named `{n}` already exists. Remove it first.")
            t = ntype.lower()
            if t not in NODE_TYPES:
                raise NodeError("Type must be `lxc`, `docker` or `kvm`.")
            u, host, p = parse_target(target)
            node = {"name": n, "type": t, "host": host, "port": port or p or 22, "user": user or u or "root",
                    "auth": "key", "enabled": True, "max_vps": 0, "pool": "", "sshx_url": "",
                    "added_by": str(ctx.author.id), "added": datetime.now().isoformat()}
            if not USER_RE.match(node["user"]):
                raise NodeError("Invalid SSH username.")
            pub = await ensure_key()
            msg = await ctx.send(embed=m.create_info_embed("Connecting", f"⏳ Testing `{node['user']}@{host}:{node['port']}` …"))
            NODES[n] = node
            h = await refresh_node(node)
            if not h.get("up"):
                NODES.pop(n, None)
                HEALTH.pop(n, None)
                await msg.edit(embed=m.create_error_embed("Could Not Connect",
                    f"{h.get('error')}\n\nRun this on the node, then try again:\n```bash\n{join_script(pub, 'pinggy' in host)}\n```"))
                return
            if t not in h["stats"]["guests"]:
                await ctx.send(embed=m.create_warning_embed("Runtime Not Found",
                    f"Connected, but `{t}` does not seem to be installed on `{n}` (found: {', '.join(h['stats']['guests']) or 'none'}). "
                    "The node was added; install the runtime before deploying."))
            save_nodes()
            container_cache["t"] = 0.0
            t_, v_ = node_card(node, public=False)
            embed = m.create_success_embed("Node Added", f"**{t_}**\n{v_}")
            await msg.edit(embed=embed)
        await run_cmd(ctx, go())

    class PassModal(discord.ui.Modal, title="Node SSH password"):
        password = discord.ui.TextInput(label="Password", style=discord.TextStyle.short, max_length=200)

        def __init__(self, node_name):
            super().__init__()
            self.node_name = node_name

        async def on_submit(self, interaction: discord.Interaction):
            node = NODES.get(self.node_name)
            if not node:
                return await interaction.response.send_message("That node no longer exists.", ephemeral=True)
            node["auth"], node["password"] = "password", str(self.password.value)
            await interaction.response.defer(ephemeral=True, thinking=True)
            h = await refresh_node(node)
            if h.get("up"):
                save_nodes()
                await interaction.followup.send(embed=m.create_success_embed("Password Saved", f"Logged in to `{node['name']}`."), ephemeral=True)
            else:
                node["auth"] = "key"
                node.pop("password", None)
                await interaction.followup.send(embed=m.create_error_embed("Login Failed", str(h.get("error"))), ephemeral=True)

    class PassView(discord.ui.View):
        def __init__(self, author_id, node_name):
            super().__init__(timeout=120)
            self.author_id, self.node_name = author_id, node_name

        @discord.ui.button(label="Enter password", style=discord.ButtonStyle.primary, emoji="🔑")
        async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author_id:
                return await interaction.response.send_message("Not your menu.", ephemeral=True)
            await interaction.response.send_modal(PassModal(self.node_name))

    @node_group.command(name="setpass")
    @m.is_main_admin()
    async def node_setpass(ctx, name: str):
        async def go():
            n = need_node(name)
            if n.get("local"):
                raise NodeError("The local node needs no password.")
            if not shutil.which("sshpass"):
                raise NodeError("Password login needs `sshpass` on this server: `apt install -y sshpass`. Key login is safer and needs nothing extra.")
            await ctx.send(embed=m.create_warning_embed("Password Login",
                "The password is saved in this bot's database (`vps.db`) in plain text — keep that file private. "
                "Key login (`.node join`) avoids this."), view=PassView(ctx.author.id, n["name"]))
        await run_cmd(ctx, go())

    @node_group.command(name="list")
    @m.is_admin()
    async def node_list(ctx):
        async def go():
            for n in all_nodes():
                if n["name"] not in HEALTH:
                    await refresh_node(n)
            await ctx.send(embed=board_embed(public=False))
        await run_cmd(ctx, go())

    @node_group.command(name="info")
    @m.is_admin()
    async def node_info(ctx, name: str):
        async def go():
            n = need_node(name)
            await refresh_node(n)
            t, v = node_card(n, public=False)
            await ctx.send(embed=m.create_info_embed(t, v))
        await run_cmd(ctx, go())

    @node_group.command(name="test")
    @m.is_admin()
    async def node_test(ctx, name: str):
        async def go():
            n = need_node(name)
            h = await refresh_node(n)
            if h.get("up"):
                await ctx.send(embed=m.create_success_embed("Node Reachable", f"🛰️ `{n['name']}` answered in **{h['latency'] * 1000:.0f} ms**."))
            else:
                await ctx.send(embed=m.create_error_embed("Node Unreachable", str(h.get("error"))))
        await run_cmd(ctx, go())

    def _toggle(flag: bool):
        async def cmd(ctx, name: str):
            async def go():
                n = need_node(name)
                if n.get("local"):
                    m.set_setting("local_deploy", "true" if flag else "false")
                else:
                    n["enabled"] = flag
                    save_nodes()
                await ctx.send(embed=m.create_success_embed("Node Updated", f"`{n['name']}` is now **{'enabled' if flag else 'disabled'}** for new deploys."))
            await run_cmd(ctx, go())
        return cmd

    node_group.command(name="enable")(m.is_admin()(_toggle(True)))
    node_group.command(name="disable")(m.is_admin()(_toggle(False)))

    @node_group.command(name="limit")
    @m.is_admin()
    async def node_limit(ctx, name: str, max_vps: int):
        async def go():
            n = need_node(name)
            if max_vps < 0 or max_vps > 10000:
                raise NodeError("Use a number from 0 (unlimited) to 10000.")
            if n.get("local"):
                m.set_setting("local_max_vps", str(max_vps))
            else:
                n["max_vps"] = max_vps
                save_nodes()
            await ctx.send(embed=m.create_success_embed("Limit Set", f"`{n['name']}` max VPS: **{max_vps or 'unlimited'}**"))
        await run_cmd(ctx, go())

    @node_group.command(name="pool")
    @m.is_admin()
    async def node_pool(ctx, name: str, pool: str):
        async def go():
            n = need_node(name)
            if n.get("local") or n["type"] != "lxc":
                raise NodeError("Storage pool applies to remote LXC nodes only (local uses `storage_pool` from config).")
            if not SAFE_NAME.match(pool):
                raise NodeError("Invalid pool name.")
            n["pool"] = pool
            save_nodes()
            await ctx.send(embed=m.create_success_embed("Pool Set", f"`{n['name']}` builds on LXD pool `{pool}`."))
        await run_cmd(ctx, go())

    @node_group.command(name="default")
    @m.is_admin()
    async def node_default(ctx, name: str):
        async def go():
            if name.lower() in ("off", "none", "auto"):
                m.set_setting("default_node", "")
                return await ctx.send(embed=m.create_success_embed("Auto Balance", "Deploys now go to the least-loaded node."))
            n = need_node(name)
            if n["type"] == "kvm":
                raise NodeError("KVM nodes cannot auto-create VPS yet.")
            m.set_setting("default_node", n["name"])
            await ctx.send(embed=m.create_success_embed("Default Node", f"New VPS go to `{n['name']}` while it is online and has room."))
        await run_cmd(ctx, go())

    @node_group.command(name="sshx")
    @m.is_main_admin()
    async def node_sshx(ctx, name: str, url: str):
        async def go():
            n = need_node(name)
            if n.get("local"):
                raise NodeError("Use this for remote nodes.")
            if not m.SSHX_URL_RE.fullmatch(url.strip()):
                raise NodeError("That is not an SSHX link (`https://sshx.io/s/...#key`). On the node run `curl -sSf https://sshx.io/get | sh -s run`.")
            n["sshx_url"] = url.strip()
            save_nodes()
            await ctx.send(embed=m.create_success_embed("SSHX Saved", f"Open it with `{m.PREFIX}node terminal {n['name']}`. SSHX is a web terminal, so the bot cannot automate with it — SSH/Pinggy handle the automation."))
        await run_cmd(ctx, go())
        try:
            await ctx.message.delete()           # the link contains the access key
        except Exception:
            pass

    @node_group.command(name="terminal")
    @m.is_main_admin()
    async def node_terminal(ctx, name: str):
        async def go():
            n = need_node(name)
            if not n.get("sshx_url"):
                raise NodeError(f"No SSHX link saved for `{n['name']}`. Use `{m.PREFIX}node sshx {n['name']} <url>`.")
            try:
                await ctx.author.send(embed=m.create_info_embed("SSHX Terminal", f"🖥️ `{n['name']}`\n{n['sshx_url']}"))
                await ctx.send(embed=m.create_success_embed("Sent", "The terminal link is in your DMs."))
            except discord.Forbidden:
                raise NodeError("Your DMs are closed, so the link cannot be sent safely.")
        await run_cmd(ctx, go())

    @node_group.command(name="board")
    @m.is_admin()
    async def node_board(ctx, channel: discord.TextChannel = None):
        async def go():
            if channel is None:
                m.set_setting("node_board_channel", "0")
                return await ctx.send(embed=m.create_success_embed("Board Off", "The live node board is disabled."))
            gaps = await m._channel_permission_gaps(channel)
            if gaps:
                raise NodeError("The bot is missing: " + ", ".join(gaps))
            m.set_setting("node_board_channel", str(channel.id))
            m.set_setting("node_board_msg", "0")
            await update_board()
            await ctx.send(embed=m.create_success_embed("Board Live", f"Node board posted in {channel.mention}; it refreshes every minute."))
        await run_cmd(ctx, go())

    @node_group.command(name="scan")
    @m.is_admin()
    async def node_scan(ctx, name: str):
        async def go():
            n = need_node(name)
            procs, findings, ports = await host_scan(n)
            if findings:
                lines = [f"PID {f['pid']} — {f['reason']}: {f['cmd'][:80]}" for f in findings[:8]]
                await ctx.send(embed=m.create_warning_embed("Mining-Like Processes",
                    f"⛏️ `{n['name']}` host: {len(procs)} processes scanned.\n```\n" + "\n".join(lines) + "\n```\nReport only — nothing was killed."))
            else:
                extra = f"\nPool-style ports open: `{ports}`" if ports else ""
                await ctx.send(embed=m.create_success_embed("Clean", f"⛏️ `{n['name']}` host: {len(procs)} processes scanned, no miner patterns.{extra}"))
        await run_cmd(ctx, go())

    @node_group.command(name="exec")
    @m.is_main_admin()
    async def node_exec(ctx, name: str, *, command: str):
        async def go():
            n = need_node(name)
            m.logger.warning(f"node exec by {ctx.author} ({ctx.author.id}) on {n['name']}: {command[:200]}")
            rc, out, err = await node_run(n, command, 60)
            text = (out + (("\n" + err) if err.strip() else "")).strip() or "(no output)"
            await ctx.send(embed=(m.create_success_embed if rc == 0 else m.create_warning_embed)(
                f"Exit {rc} • {n['name']}", f"```\n{text[:1800]}\n```"))
        await run_cmd(ctx, go())

    @node_group.command(name="remove")
    @m.is_main_admin()
    async def node_remove(ctx, name: str, force: str = ""):
        async def go():
            n = need_node(name)
            if n.get("local"):
                raise NodeError("The local node cannot be removed (disable it with `.node disable local`).")
            mine = vps_count(n["name"])
            if mine and force.lower() != "force":
                raise NodeError(f"`{n['name']}` still hosts **{mine}** VPS. Move or delete them first, or run `{m.PREFIX}node remove {n['name']} force` (the VPS records stay but cannot be managed until you re-add the node).")
            NODES.pop(n["name"], None)
            HEALTH.pop(n["name"], None)
            save_nodes()
            if m.get_setting("default_node", "") == n["name"]:
                m.set_setting("default_node", "")
            await ctx.send(embed=m.create_success_embed("Node Removed", f"`{n['name']}` was removed."))
        await run_cmd(ctx, go())

    m.logger.info(f"Legacy Cloud node layer loaded • nodes={len(NODES)}")
    return ON_CREATED
