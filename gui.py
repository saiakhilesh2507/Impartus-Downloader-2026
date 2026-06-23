#!/usr/bin/env python3
"""
gui.py - local web GUI for the Impartus lecture downloader.

Launch:
    python gui.py                       # opens http://127.0.0.1:5050
    python gui.py --port 8080
    python gui.py --no-browser --out ./lectures

The web UI is a single-page app: login -> subjects -> lectures (with mic icons
showing whether audio was recorded) -> checkbox-select -> download.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request

from impartus import (
    BANDWIDTH_480, BANDWIDTH_720,
    Impartus, Lecture, Manifest,
    clear_session, load_session,
)
from impartus.download import download_lecture

app = Flask(__name__)
app.secret_key = os.urandom(32)

_state_lock = threading.Lock()
_state = {
    "client": None,
    "user": None,
    "progress": {},        # {ttid: {phase, current, total, message, file, updated_at}}
    "out_dir": "./impartus_downloads",
    "quality": "720",
}


def _update_progress(ttid, phase, current, total, message, file_path):
    with _state_lock:
        _state["progress"][str(ttid)] = {
            "ttid": ttid, "phase": phase, "current": current,
            "total": total, "message": message, "file": file_path,
            "updated_at": time.time(),
        }


def _do_download_lecture(c, lec, manifest, prefer_bandwidth):
    """Thin wrapper that pipes progress events into _update_progress."""
    download_lecture(
        c, lec, manifest,
        prefer_bandwidth=prefer_bandwidth,
        progress_cb=_update_progress,
        quiet=True,
    )


def _run_downloads_background(jobs, out_dir, quality):
    with _state_lock:
        c = _state["client"]
    if c is None:
        return
    manifest = Manifest(Path(out_dir))
    prefer = {"480": BANDWIDTH_480, "720": BANDWIDTH_720}.get(quality, "")
    for lec in jobs:
        try:
            _do_download_lecture(c, lec, manifest, prefer)
        except Exception as e:
            _update_progress(lec.ttid, "error", 0, 0, f"{type(e).__name__}: {e}", None)


def _require_login():
    """Return an authenticated Impartus() client, hydrating from cache if possible."""
    with _state_lock:
        c = _state["client"]
    if c is None:
        cached = load_session()
        if cached.get("token"):
            c = Impartus()
            c.token = cached["token"]
            if c.try_cached_token():
                with _state_lock:
                    _state["client"] = c
                    _state["user"] = cached.get("username")
            else:
                c = None
    return c


# ----------------- HTML frontend -----------------

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Impartus Lecture Downloader</title>
<style>
:root {
  --bg: #0f1115; --panel: #1a1d24; --panel2: #232831;
  --text: #e8eaf0; --muted: #8a91a0; --accent: #5b8def; --accent2: #4477d6;
  --ok: #44c47a; --warn: #e8b94a; --err: #e8625a; --border: #2c313b;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  font-size: 14px; }
.app { display: flex; flex-direction: column; height: 100vh; }
.topbar { display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; border-bottom: 1px solid var(--border); background: var(--panel); }
.topbar h1 { font-size: 17px; font-weight: 600; margin: 0; }
.topbar .who { color: var(--muted); font-size: 13px; }
.topbar button.logout { background: transparent; color: var(--muted); border: 1px solid var(--border);
  padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
.topbar button.logout:hover { color: var(--text); border-color: var(--accent); }
.main { flex: 1; display: flex; overflow: hidden; }
.sidebar { width: 280px; border-right: 1px solid var(--border); background: var(--panel);
  overflow-y: auto; padding: 12px 0; }
.sidebar .group-label { padding: 12px 16px 4px; color: var(--muted); font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.06em; }
.subject { padding: 10px 16px; cursor: pointer; border-left: 3px solid transparent;
  display: flex; flex-direction: column; gap: 2px; }
.subject:hover { background: var(--panel2); }
.subject.active { background: var(--panel2); border-left-color: var(--accent); }
.subject .name { font-weight: 500; }
.subject .meta { font-size: 11px; color: var(--muted); }
.content { flex: 1; overflow-y: auto; padding: 20px 24px; }
.empty { color: var(--muted); padding: 40px; text-align: center; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px;
  padding-bottom: 12px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.toolbar h2 { margin: 0; font-size: 16px; font-weight: 600; flex: 1; }
.toolbar .pill { background: var(--panel2); border: 1px solid var(--border);
  padding: 5px 11px; border-radius: 999px; font-size: 12px; color: var(--muted); }
.toolbar .legend { display: flex; align-items: center; gap: 6px; font-size: 11px;
  color: var(--muted); padding: 4px 10px; border: 1px solid var(--border);
  border-radius: 6px; background: var(--panel2); }
.toolbar .legend .swatch { font-size: 13px; }
.toolbar select, .toolbar button { background: var(--panel2); color: var(--text);
  border: 1px solid var(--border); padding: 6px 12px; border-radius: 6px; font-size: 13px;
  cursor: pointer; }
.toolbar button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.toolbar button.primary:hover { background: var(--accent2); }
.toolbar button:disabled { opacity: 0.5; cursor: not-allowed; }
.toolbar input[type=text] { background: var(--panel2); color: var(--text);
  border: 1px solid var(--border); padding: 6px 12px; border-radius: 6px; font-size: 13px; }
.lecture { display: grid; grid-template-columns: 30px 26px 130px 1fr 200px 80px;
  align-items: center; gap: 10px; padding: 9px 12px; border-bottom: 1px solid var(--border); }
.lecture:hover { background: var(--panel2); }
.lecture input[type=checkbox] { width: 16px; height: 16px; cursor: pointer; }
.lecture .date { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; }
.lecture .topic { font-weight: 500; }
.lecture .prof { color: var(--muted); font-size: 12px; }
.lecture .badge { font-size: 10px; color: var(--muted); text-align: right; }
.lecture .mic { font-size: 14px; text-align: center; cursor: help; user-select: none; }
.lecture .mic.has-audio { color: var(--ok); }
.lecture .mic.no-audio { color: var(--muted); opacity: 0.35; }
.lecture .progress { grid-column: 1 / -1; height: 4px; background: var(--panel2);
  border-radius: 2px; overflow: hidden; display: none; }
.lecture.active .progress { display: block; }
.lecture .progress .bar { height: 100%; background: var(--accent);
  transition: width 0.3s; }
.lecture.done { opacity: 0.7; }
.lecture.error .progress .bar { background: var(--err); }
.lecture .status-msg { grid-column: 1 / -1; font-size: 11px; color: var(--muted);
  padding-left: 40px; }
.login-wrap { display: flex; align-items: center; justify-content: center;
  height: 100vh; padding: 20px; }
.login { background: var(--panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 30px; max-width: 360px; width: 100%; }
.login h1 { margin: 0 0 6px; font-size: 20px; }
.login .sub { color: var(--muted); margin-bottom: 22px; font-size: 13px; }
.login label { display: block; margin-bottom: 4px; font-size: 12px; color: var(--muted); }
.login input { width: 100%; padding: 9px 12px; background: var(--panel2); color: var(--text);
  border: 1px solid var(--border); border-radius: 6px; font-size: 14px; margin-bottom: 14px; }
.login input:focus { outline: none; border-color: var(--accent); }
.login button { width: 100%; padding: 10px; background: var(--accent); color: #fff;
  border: none; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; }
.login button:hover { background: var(--accent2); }
.login button:disabled { opacity: 0.6; }
.login .err { color: var(--err); font-size: 13px; margin-top: 10px; min-height: 18px; }
.login .hint { color: var(--muted); font-size: 11px; margin-top: 14px; line-height: 1.5; }
.files { margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--border); }
.files h3 { font-size: 14px; margin: 0 0 10px; color: var(--muted); font-weight: 500; }
.files .file { display: flex; justify-content: space-between; padding: 6px 10px;
  font-size: 12px; color: var(--muted); border-bottom: 1px dashed var(--border); }
.files .file .sz { font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<div class="app">
  <div id="loginView" class="login-wrap" style="display:none">
    <form class="login" id="loginForm">
      <h1>Impartus Downloader</h1>
      <div class="sub">Sign in to your college Impartus account.</div>
      <label>Username</label>
      <input id="user" type="text" autocomplete="username" required>
      <label>Password</label>
      <input id="pass" type="password" autocomplete="current-password" required>
      <button id="loginBtn" type="submit">Sign in</button>
      <div class="err" id="loginErr"></div>
      <div class="hint">Credentials are sent to <code>https://a.impartus.com/api/auth/signin</code>.
        Only the bearer token is cached locally (in %APPDATA%/impartus-dl/session.json).
        Password is never written to disk.</div>
    </form>
  </div>

  <div id="appView" style="display:none">
    <div class="topbar">
      <h1>📚 Impartus Downloader</h1>
      <div>
        <span class="who" id="who"></span>
        &nbsp;
        <button class="logout" id="logoutBtn">Sign out</button>
      </div>
    </div>
    <div class="main">
      <div class="sidebar" id="sidebar">
        <div class="group-label">Subjects</div>
        <div id="subjectList"><div class="empty">Loading…</div></div>
      </div>
      <div class="content" id="content">
        <div class="empty">Pick a subject from the left to see its lectures.</div>
      </div>
    </div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const fmtBytes = (b) => {
  if (b == null) return "?";
  const units = ["B","KB","MB","GB"];
  let i = 0;
  while (b >= 1024 && i < units.length-1) { b /= 1024; i++; }
  return b.toFixed(i ? 1 : 0) + " " + units[i];
};
const fmtDate = (s) => s ? s.replace(" ", " ").replace(/^(\d{4})-(\d{2})-(\d{2})/, "$3/$2/$1") : "—";

let subjects = [];
let currentSubject = null;
let lectures = [];
let progress = {};
let pollTimer = null;

async function api(path, opts) {
  const r = await fetch(path, Object.assign({headers:{"Content-Type":"application/json"}}, opts||{}));
  const t = await r.text();
  let data; try { data = t ? JSON.parse(t) : null; } catch { data = t; }
  if (!r.ok) throw new Error((data && data.error) || (typeof data === "string" ? data : r.statusText));
  return data;
}

async function init() {
  try {
    const who = await api("/api/whoami");
    if (who.user) { showApp(who); return; }
  } catch (e) {}
  showLogin();
}

function showLogin() {
  $("loginView").style.display = "";
  $("appView").style.display = "none";
  $("loginForm").onsubmit = async (e) => {
    e.preventDefault();
    const u = $("user").value.trim();
    const p = $("pass").value;
    $("loginBtn").disabled = true;
    $("loginErr").textContent = "";
    try {
      const r = await api("/api/login", {method:"POST", body: JSON.stringify({username:u, password:p})});
      showApp(r);
    } catch (err) {
      $("loginErr").textContent = err.message;
    } finally {
      $("loginBtn").disabled = false;
    }
  };
}

async function showApp(who) {
  $("loginView").style.display = "none";
  $("appView").style.display = "";
  $("who").textContent = who.fullname + " · " + who.email + " · " + who.sessionName;
  await loadSubjects();
  startPolling();
}

$("logoutBtn").onclick = async () => {
  if (!confirm("Sign out and forget cached token?")) return;
  await api("/api/logout", {method:"POST"});
  stopPolling();
  init();
};

async function loadSubjects() {
  subjects = await api("/api/subjects");
  const list = $("subjectList");
  if (!subjects.length) { list.innerHTML = '<div class="empty">No subjects</div>'; return; }
  const groups = {};
  subjects.forEach(s => { (groups[s.sessionName] = groups[s.sessionName] || []).push(s); });
  list.innerHTML = "";
  Object.keys(groups).forEach(sess => {
    const lbl = document.createElement("div");
    lbl.className = "group-label";
    lbl.textContent = sess;
    list.appendChild(lbl);
    groups[sess].forEach(s => {
      const div = document.createElement("div");
      div.className = "subject";
      div.dataset.id = s.subjectId;
      div.innerHTML = `<div class="name">${escapeHtml(s.subjectName)}</div>
                       <div class="meta">${s.lecturesCount} lectures · id ${s.subjectId}</div>`;
      div.onclick = () => selectSubject(s);
      list.appendChild(div);
    });
  });
}

async function selectSubject(s) {
  document.querySelectorAll(".subject").forEach(el => el.classList.toggle("active",
    +el.dataset.id === s.subjectId));
  currentSubject = s;
  const content = $("content");
  content.innerHTML = `<div class="empty">Loading lectures…</div>`;
  try {
    lectures = await api(`/api/subjects/${s.subjectId}/lectures`);
  } catch (e) {
    content.innerHTML = `<div class="empty">Failed: ${escapeHtml(e.message)}</div>`;
    return;
  }
  renderLectures();
}

function renderLectures() {
  const content = $("content");
  content.innerHTML = `
    <div class="toolbar">
      <h2>${escapeHtml(currentSubject.subjectName)}</h2>
      <input type="text" id="searchBox" placeholder="Filter…" style="min-width:180px">
      <span class="pill" id="countPill">${lectures.length} lectures</span>
      <span class="legend"><span class="swatch has-audio" style="color:var(--ok)">🎙️</span> audio</span>
      <span class="legend"><span class="swatch no-audio" style="color:var(--muted);opacity:0.35">🎙️</span> no audio</span>
      <select id="qualitySel">
        <option value="480">480p (smaller)</option>
        <option value="720" selected>720p (default)</option>
        <option value="highest">Highest available</option>
      </select>
      <button id="selectAll">Select all</button>
      <button id="selectNone">Clear</button>
      <button id="selectMissing">Undownloaded only</button>
      <button class="primary" id="downloadBtn">⬇ Download selected</button>
    </div>
    <div id="lectureList"></div>
    <div class="files" id="filesSection" style="display:none">
      <h3>Downloaded files in this folder</h3>
      <div id="filesList"></div>
    </div>
  `;
  $("searchBox").oninput = drawLectureList;
  $("selectAll").onclick = () => { document.querySelectorAll(".lecture input[type=checkbox]").forEach(cb => cb.checked = true); updateCount(); };
  $("selectNone").onclick = () => { document.querySelectorAll(".lecture input[type=checkbox]").forEach(cb => cb.checked = false); updateCount(); };
  $("selectMissing").onclick = () => { document.querySelectorAll(".lecture").forEach(el => {
    const ttid = +el.dataset.ttid; const cb = el.querySelector("input[type=checkbox]");
    cb.checked = !(progress[ttid] && progress[ttid].phase === "done");
  }); updateCount(); };
  $("downloadBtn").onclick = startDownloads;
  drawLectureList();
  loadFiles();
}

function drawLectureList() {
  const q = ($("searchBox").value || "").toLowerCase();
  const filtered = lectures.filter(L =>
    !q || (L.topic && L.topic.toLowerCase().includes(q)) ||
    (L.professorName && L.professorName.toLowerCase().includes(q)) ||
    (L.startTime && L.startTime.includes(q))
  );
  const list = $("lectureList");
  list.innerHTML = "";
  filtered.forEach(L => {
    const p = progress[L.ttid] || {};
    const phase = p.phase || "";
    const isDone = phase === "done";
    const isError = phase === "error";
    const isActive = !isDone && phase !== "";
    const pct = (p.current != null && p.total) ? Math.round(100*p.current/p.total) : 0;
    const div = document.createElement("div");
    div.className = "lecture" + (isDone?" done":"") + (isActive?" active":"") + (isError?" error":"");
    div.dataset.ttid = L.ttid;
    div.innerHTML = `
      <input type="checkbox" ${isDone?"disabled":""}>
      <div class="mic ${L.noaudio==1 ? 'no-audio' : 'has-audio'}" title="${L.noaudio==1 ? 'No audio recorded' : 'Audio recorded'}">🎙️</div>
      <div class="date">${fmtDate(L.startTime)}</div>
      <div>
        <div class="topic">${escapeHtml(L.topic || "No topic")}</div>
        <div class="prof">${escapeHtml(L.professorName || "")}</div>
      </div>
      <div class="badge">${isDone ? "✓ done" : isError ? "✗ error" : isActive ? phase : ""}</div>
      <div></div>
      <div class="progress"><div class="bar" style="width:${pct}%"></div></div>
      ${isActive || isError ? `<div class="status-msg">${escapeHtml(p.message || phase)}</div>` : ""}
    `;
    div.querySelector("input[type=checkbox]").onchange = updateCount;
    list.appendChild(div);
  });
  $("countPill").textContent = `${filtered.length} lectures`;
  updateCount();
}

function updateCount() {
  const n = [...document.querySelectorAll(".lecture input[type=checkbox]:checked")].length;
  $("downloadBtn").textContent = n ? `⬇ Download ${n} selected` : "⬇ Download selected";
  $("downloadBtn").disabled = n === 0;
}

async function startDownloads() {
  const selected = [...document.querySelectorAll(".lecture input[type=checkbox]:checked")].map(cb => +cb.closest(".lecture").dataset.ttid);
  if (!selected.length) return;
  const quality = $("qualitySel").value;
  $("downloadBtn").disabled = true;
  const origText = $("downloadBtn").textContent;
  $("downloadBtn").textContent = "Queued…";
  try {
    const r = await api("/api/download", {method:"POST", body: JSON.stringify({ttids:selected, quality})});
    if (r.queued === 0 && r.skipped_done > 0) {
      $("downloadBtn").textContent = `All ${r.skipped_done} already downloaded ✓`;
      setTimeout(() => updateCount(), 2500);
    } else {
      $("downloadBtn").textContent = `⬇ Queued ${r.queued} download${r.queued===1?"":"s"}`;
      setTimeout(() => updateCount(), 4000);
    }
  } catch (e) {
    alert("Failed to queue: " + e.message);
    $("downloadBtn").disabled = false;
    $("downloadBtn").textContent = origText;
    updateCount();
  }
}

async function loadFiles() {
  try {
    const files = await api("/api/files");
    if (files.length) {
      $("filesSection").style.display = "";
      $("filesList").innerHTML = files.map(f =>
        `<div class="file"><span>${escapeHtml(f.name)}</span><span class="sz">${fmtBytes(f.size)}</span></div>`
      ).join("");
    }
  } catch (e) {}
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const p = await api("/api/progress");
      let changed = false;
      for (const k of Object.keys(p)) {
        if (JSON.stringify(progress[k]) !== JSON.stringify(p[k])) {
          progress[k] = p[k]; changed = true;
        }
      }
      if (changed && currentSubject) drawLectureList();
    } catch (e) {}
  }, 1000);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function escapeHtml(s) {
  return String(s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
}

init();
</script>
</body>
</html>
"""


# ----------------- routes -----------------

@app.route("/")
def index():
    return INDEX_HTML


@app.route("/api/whoami")
def whoami():
    c = _require_login()
    if c is None:
        return jsonify({"user": None}), 401
    prof = c.profile()
    return jsonify({
        "user": _state.get("user") or prof.get("email"),
        "fullname": prof.get("fullname"),
        "email": prof.get("email"),
        "sessionName": prof.get("sessionName"),
        "departmentName": prof.get("departmentName"),
    })


@app.route("/api/login", methods=["POST"])
def login():
    from impartus import save_session
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    c = Impartus()
    try:
        signin = c.signin(username, password)
    except Exception as e:
        return jsonify({"error": str(e)}), 401
    save_session(username, c.token)
    with _state_lock:
        _state["client"] = c
        _state["user"] = username
    prof = c.profile()
    return jsonify({
        "user": username,
        "fullname": prof.get("fullname"),
        "email": prof.get("email"),
        "sessionName": prof.get("sessionName"),
        "departmentName": prof.get("departmentName"),
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    clear_session()
    with _state_lock:
        _state["client"] = None
        _state["user"] = None
        _state["progress"] = {}
    return jsonify({"ok": True})


@app.route("/api/subjects")
def subjects():
    c = _require_login()
    if c is None:
        return jsonify({"error": "not logged in"}), 401
    subs = c.list_subjects()
    out = []
    for s in subs:
        ll = s.get("lastLecture", {}) or {}
        out.append({
            "subjectId": s["subjectId"],
            "subjectName": s.get("subjectName"),
            "sessionId": s.get("sessionId"),
            "sessionName": s.get("sessionName"),
            "lecturesCount": "?",
            "lastLecture": {
                "ttid": ll.get("ttid"),
                "topic": ll.get("topic"),
                "startTime": ll.get("startTime"),
            },
        })
    return jsonify(out)


@app.route("/api/subjects/<int:sid>/lectures")
def subject_lectures(sid):
    c = _require_login()
    if c is None:
        return jsonify({"error": "not logged in"}), 401
    subs = c.list_subjects()
    sess = next((s for s in subs if s["subjectId"] == sid), None)
    if not sess:
        return jsonify({"error": "subject not found"}), 404
    lecs = c.list_lectures(sid, sess["sessionId"])
    return jsonify(lecs)


@app.route("/api/download", methods=["POST"])
def start_download():
    c = _require_login()
    if c is None:
        return jsonify({"error": "not logged in"}), 401
    data = request.get_json(silent=True) or {}
    ttids = data.get("ttids") or []
    quality = data.get("quality") or "720"
    out_dir = str(Path(data.get("out_dir") or _state["out_dir"]).resolve())

    if not ttids:
        return jsonify({"error": "no ttids selected"}), 400

    print(f"[download] ttids={ttids} quality={quality} out_dir={out_dir}",
          file=sys.stderr, flush=True)

    try:
        subs = c.list_subjects()
    except Exception as e:
        return jsonify({"error": f"list_subjects failed: {e}"}), 500

    manifest = Manifest(Path(out_dir))
    jobs = []
    skipped_done = 0
    for ttid in ttids:
        if manifest.is_done(ttid):
            prev = Path(manifest.data[str(ttid)]["file"])
            if prev.exists():
                skipped_done += 1
                _update_progress(ttid, "done", 1, 1,
                                 f"already downloaded: {prev.name}", str(prev))
                continue
        lec = None
        subj = None
        for s in subs:
            try:
                lecs = c.list_lectures(s["subjectId"], s["sessionId"])
            except Exception as ex:
                print(f"[download] list_lectures({s['subjectId']}) failed: {ex}",
                      file=sys.stderr, flush=True)
                continue
            for L in lecs:
                if L["ttid"] == ttid:
                    lec = L
                    subj = s
                    break
            if lec:
                break
        if lec is None:
            _update_progress(ttid, "error", 0, 0, "ttid not found in your subjects", None)
            continue
        jobs.append(Lecture(
            ttid=lec["ttid"], video_id=lec.get("videoId", 0),
            title=lec.get("topic") or f"lecture_{ttid}",
            subject=subj.get("subjectName", "?"),
            professor=lec.get("professorName", "?"),
            start_time=lec.get("startTime", ""),
            duration_s=int(lec.get("actualDuration") or 0),
        ))

    if not jobs and skipped_done == 0:
        return jsonify({"error": "no resolvable ttids (not in your subjects?)"}), 400

    with _state_lock:
        _state["out_dir"] = out_dir
        _state["quality"] = quality

    t = threading.Thread(target=_run_downloads_background,
                         args=(jobs, out_dir, quality), daemon=True)
    t.start()
    return jsonify({"ok": True, "queued": len(jobs), "skipped_done": skipped_done})


@app.route("/api/progress")
def progress():
    with _state_lock:
        cutoff = time.time() - 600
        out = {k: v for k, v in _state["progress"].items()
               if v.get("updated_at", 0) > cutoff or v.get("phase") in ("done", "error")}
        return jsonify(out)


@app.route("/api/files")
def list_files():
    out_dir = Path(_state["out_dir"])
    if not out_dir.exists():
        return jsonify([])
    files = []
    for p in out_dir.iterdir():
        if (p.is_file() and not p.name.startswith(".")
                and p.suffix.lower() in (".mp4", ".ts", ".mkv")):
            files.append({"name": p.name, "size": p.stat().st_size})
    files.sort(key=lambda x: x["name"])
    return jsonify(files)


# ----------------- main -----------------

def main():
    parser = argparse.ArgumentParser(
        description="Local web GUI for the Impartus lecture downloader.",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1 = localhost only)")
    parser.add_argument("--port", type=int, default=5050, help="Port (default: 5050)")
    parser.add_argument("--out", default="./impartus_downloads",
                        help="Download output directory")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't auto-open browser")
    args = parser.parse_args()

    _state["out_dir"] = args.out

    url = f"http://{args.host}:{args.port}"
    print(f"Starting Impartus Downloader GUI at {url}")
    print(f"Output directory: {os.path.abspath(args.out)}")
    print(f"Press Ctrl+C to stop.")
    if not args.no_browser:
        def open_browser():
            time.sleep(0.8)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=open_browser, daemon=True).start()

    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()