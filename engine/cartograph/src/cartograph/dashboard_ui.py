"""
Dashboard HTML/CSS/JS template for Cartograph.

This is the single-page application served by the dashboard server.
Separated from dashboard.py to keep the server logic readable.
"""

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cartograph</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'%3E%3Crect width='200' height='200' rx='40' fill='%231a1d27'/%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0%25' y1='0%25' x2='0%25' y2='100%25'%3E%3Cstop offset='0%25' stop-color='%23FFF'/%3E%3Cstop offset='50%25' stop-color='%23E4E4E7'/%3E%3Cstop offset='100%25' stop-color='%23A1A1AA'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cpath d='M 125,45 A 60 60 0 1 0 125,155' fill='none' stroke='url(%23g)' stroke-width='24' stroke-linecap='round'/%3E%3Crect x='88' y='88' width='24' height='24' rx='4' fill='%23FFEA00'/%3E%3Crect x='135' y='92' width='16' height='16' rx='3' fill='%23FFC300'/%3E%3Crect x='175' y='95' width='10' height='10' rx='2' fill='%23FF9500'/%3E%3C/svg%3E">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/nim.min.js"></script>
<script>hljs.configure({ignoreUnescapedHTML: true});</script>
<style>
  /* Override hljs background to match our theme */
  .hljs { background: var(--surface) !important; padding: 0 !important; }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #fafafa;
    --surface:   #ffffff;
    --surface2:  #f3f4f6;
    --border:    #e5e7eb;
    --text:      #1f2937;
    --muted:     #6b7280;
    --accent:    #D4A017;
    --accent-light: #FFEA00;
    --accent-deep: #FF9500;
    --green:     #059669;
    --orange:    #d97706;
    --red:       #dc2626;
    --purple:    #7c3aed;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
    --shadow:    0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 4px 6px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.04);
    --radius:    8px;
  }

  [data-theme="dark"] {
    --bg:        #0f1117;
    --surface:   #1a1d27;
    --surface2:  #22252f;
    --border:    #2e3240;
    --text:      #e4e6ed;
    --muted:     #8b8fa3;
    --accent:    #FFCE45;
    --accent-light: #FFEA00;
    --accent-deep: #FF9500;
    --green:     #34d399;
    --orange:    #f0a445;
    --red:       #f06060;
    --purple:    #a78bfa;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.2);
    --shadow:    0 1px 3px rgba(0,0,0,0.3), 0 1px 2px rgba(0,0,0,0.2);
    --shadow-md: 0 4px 6px rgba(0,0,0,0.25), 0 2px 4px rgba(0,0,0,0.15);
  }

  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.5; }

  /* ── Nav ── */
  nav {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    box-shadow: var(--shadow-sm);
    padding: 0 24px;
    height: 56px;
    display: flex;
    align-items: center;
    gap: 16px;
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .nav-logo { font-weight: 700; font-size: 18px; color: var(--text); letter-spacing: -0.5px; cursor: pointer; flex-shrink: 0; display: flex; align-items: center; gap: 10px; }
  .nav-logo-mark { width: 34px; height: 34px; background: #1a1d27; border-radius: 7px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
  .nav-logo-mark svg { width: 28px; height: 28px; }


  .nav-tabs { display: flex; gap: 0; margin-left: 24px; height: 100%; }
  .nav-tab {
    padding: 0 16px; height: 100%; display: flex; align-items: center; gap: 6px;
    font-size: 14px; color: var(--muted); cursor: pointer;
    border: none; background: none; border-bottom: 2px solid transparent;
    transition: color 0.1s;
  }
  .nav-tab:hover { color: var(--text); }
  .nav-tab.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 500; }

  .nav-right { display: flex; align-items: center; gap: 10px; flex-shrink: 0; margin-left: auto; }

  .btn {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--muted);
    padding: 6px 14px;
    font-size: 13px;
    cursor: pointer;
    display: flex; align-items: center; gap: 5px;
    transition: all 0.15s;
  }
  .btn:hover { color: var(--text); border-color: #d1d5db; box-shadow: var(--shadow-sm); }
  .btn.refreshing { animation: spin 0.6s linear infinite; }


  .tab-badge {
    background: var(--border);
    color: var(--muted);
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 10px;
  }
  .nav-tab.active .tab-badge { background: #D4A01718; color: var(--accent); }

  /* ── Content ── */
  .content { margin: 0 auto; padding: 28px 32px; }
  .content.view-profile, .content.view-search { max-width: 860px; }
  .content.view-detail { max-width: 1280px; }

  /* ── Search view ── */
  .search-bar {
    max-width: 600px;
    margin: 32px auto 32px;
    position: relative;
  }
  .search-bar input {
    width: 100%;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 20px 16px 48px;
    color: var(--text);
    font-size: 17px;
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
    box-shadow: var(--shadow);
  }
  .search-bar input:focus { border-color: var(--accent); box-shadow: 0 0 0 4px #D4A01715, var(--shadow); }
  .search-bar input::placeholder { color: var(--muted); }
  .search-bar-icon {
    position: absolute; left: 18px; top: 50%; transform: translateY(-50%);
    color: var(--muted); pointer-events: none;
    width: 18px; height: 18px;
  }

  /* ── Filter bar ── */
  .filter-bar {
    display: flex; gap: 10px; margin-bottom: 18px; align-items: center; flex-wrap: wrap;
  }
  .filter-bar input[type="search"] {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 7px 14px; color: var(--text); font-size: 13px; width: 220px; outline: none;
    box-shadow: var(--shadow-sm); transition: border-color 0.15s;
  }
  .filter-bar input[type="search"]:focus { border-color: var(--accent); }

  /* Segmented control */
  .seg-control {
    display: flex; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden;
    background: var(--surface); box-shadow: var(--shadow-sm);
  }
  .seg-btn {
    padding: 6px 14px; font-size: 12px; font-weight: 500; cursor: pointer;
    border: none; background: none; color: var(--muted); transition: all 0.15s;
    border-right: 1px solid var(--border); white-space: nowrap;
  }
  .seg-btn:last-child { border-right: none; }
  .seg-btn:hover { color: var(--text); }
  .seg-btn.active { background: var(--accent); color: #fff; }

  /* Multi-select dropdown */
  .multi-select {
    position: relative; display: inline-block;
  }
  .multi-select-btn {
    padding: 6px 28px 6px 12px; font-size: 12px; font-weight: 500; cursor: pointer;
    border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface);
    color: var(--muted); box-shadow: var(--shadow-sm); transition: all 0.15s;
    white-space: nowrap; position: relative;
  }
  .multi-select-btn::after {
    content: ''; position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
    border: 4px solid transparent; border-top: 5px solid var(--muted);
  }
  .multi-select-btn:hover { color: var(--text); border-color: #d1d5db; }
  .multi-select-btn.has-selection { color: var(--accent); border-color: var(--accent); }
  .multi-select-menu {
    display: none; position: absolute; top: calc(100% + 4px); left: 0; z-index: 50;
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: var(--shadow-md); min-width: 160px; padding: 4px 0;
    max-height: 240px; overflow-y: auto;
  }
  .multi-select.open .multi-select-menu { display: block; }
  .multi-select-item {
    display: flex; align-items: center; gap: 8px; padding: 6px 12px;
    font-size: 12px; color: var(--text); cursor: pointer; transition: background 0.1s;
  }
  .multi-select-item:hover { background: var(--surface2); }
  .multi-select-item input[type="checkbox"] { accent-color: var(--accent); margin: 0; }
  .multi-select-clear {
    padding: 6px 12px; font-size: 11px; color: var(--muted); cursor: pointer;
    border-top: 1px solid var(--border); text-align: center;
  }
  .multi-select-clear:hover { color: var(--accent); }

  /* ── Widget list ── */
  .widget-list { display: flex; flex-direction: column; gap: 2px; }
  .widget-card {
    padding: 14px 16px;
    border-radius: var(--radius);
    cursor: pointer;
    transition: all 0.15s;
    border: 1px solid transparent;
  }
  .widget-card:hover { background: var(--surface); border-color: var(--border); box-shadow: var(--shadow); }
  .widget-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; flex-wrap: wrap; }
  .widget-name { font-size: 15px; font-weight: 600; color: var(--text); }
  .lang-text-python     { color: #3572A5; }
  .lang-text-javascript { color: #b07d2e; }
  .lang-text-typescript { color: #2b7489; }
  .lang-text-nim        { color: #e85d00; }
  .lang-text-angular    { color: #dd0031; }
  .lang-text-php        { color: #8892bf; }
  .lang-text-openscad   { color: #f9d72c; }
  .lang-text-systemverilog { color: #178600; }
  .lang-text-terraform  { color: #7b42bc; }
  .lang-text-go         { color: #00add8; }
  .lang-text-spice      { color: #2e8b57; }
  .lang-text-rust       { color: #dea584; }
  .lang-text-gdscript   { color: #478cbf; }
  .lang-text-java       { color: #b07219; }
  .lang-text-lean       { color: #83579a; }
  .lang-text-csharp     { color: #178600; }
  .lang-text-flutter    { color: #027dfd; }
  .widget-owner { font-size: 13px; color: var(--muted); }
  .widget-version { font-size: 12px; color: var(--muted); background: var(--surface2); padding: 1px 6px; border-radius: 4px; }
  .widget-desc { font-size: 13px; color: var(--muted); line-height: 1.5; margin-bottom: 8px; max-width: 700px; }
  .widget-meta { display: flex; align-items: center; gap: 14px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }

  .lang-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .lang-python     { background: #3572A5; }
  .lang-javascript { background: #f1e05a; }
  .lang-typescript { background: #2b7489; }
  .lang-nim        { background: #e85d00; }
  .lang-angular    { background: #dd0031; }
  .lang-php        { background: #8892bf; }
  .lang-openscad   { background: #f9d72c; }
  .lang-systemverilog { background: #178600; }
  .lang-terraform  { background: #7b42bc; }
  .lang-go         { background: #00add8; }
  .lang-spice      { background: #2e8b57; }
  .lang-rust       { background: #dea584; }
  .lang-gdscript   { background: #478cbf; }
  .lang-java       { background: #b07219; }
  .lang-lean       { background: #83579a; }
  .lang-csharp     { background: #178600; }
  .lang-flutter    { background: #027dfd; }
  .lang-unknown    { background: var(--muted); }

  .domain-tag {
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 10px;
    border: 1px solid var(--border);
    color: var(--muted);
  }

  .sync-badge {
    font-size: 11px; padding: 2px 8px; border-radius: 10px;
    border: 1px solid; font-weight: 500; white-space: nowrap;
  }
  .sync-local    { color: var(--accent);   border-color: #D4A01730; background: #D4A0170a; }
  .sync-cloud    { color: var(--purple); border-color: #7c3aed30; background: #7c3aed0a; }
  .sync-published   { color: var(--green);  border-color: #05966930; background: #0596690a; }
  .sync-mismatch { color: var(--orange); border-color: #d9770630; background: #d977060a; }

  .vis-badge {
    font-size: 11px; padding: 1px 6px; border-radius: 4px;
  }
  .vis-public  { color: var(--green); background: #0596690c; }
  .vis-private { color: var(--orange); background: #d977060c; }
  .vis-local   { color: var(--accent); background: #D4A0170c; }
  .vis-cloud   { color: var(--purple); background: #7c3aed0c; }
  .vis-mismatch { color: var(--orange); background: #d977060c; }

  /* ── Detail view ── */
  .detail-back {
    font-size: 13px; color: var(--accent); cursor: pointer; margin-bottom: 16px;
    display: inline-flex; align-items: center; gap: 4px;
  }
  .detail-back:hover { text-decoration: underline; }
  .detail-title { font-size: 24px; font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .detail-owner { font-size: 15px; color: var(--muted); margin-bottom: 16px; }
  .detail-desc { font-size: 15px; color: var(--text); line-height: 1.6; margin-bottom: 24px; max-width: 700px; }

  .detail-grid {
    display: grid;
    grid-template-columns: 1fr 280px;
    gap: 24px;
    align-items: start;
  }
  @media (max-width: 900px) { .detail-grid { grid-template-columns: 1fr; } }

  .detail-main { min-width: 0; }
  .detail-sidebar {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px;
    box-shadow: var(--shadow-sm);
  }
  .detail-sidebar h3 { font-size: 12px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.5px; margin-bottom: 10px; }
  .detail-meta-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
  .detail-meta-row:last-child { border-bottom: none; }
  .detail-meta-label { color: var(--muted); }
  .detail-meta-value { color: var(--text); font-weight: 500; }

  .install-cmd {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 10px 14px;
    font-family: monospace;
    font-size: 13px;
    color: var(--green);
    margin-top: 12px;
    cursor: pointer;
    position: relative;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .install-cmd:hover { border-color: var(--green); box-shadow: var(--shadow-sm); }
  .install-cmd .copy-hint {
    position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
    font-size: 11px; color: var(--muted); font-family: -apple-system, sans-serif;
  }

  .tags-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .tag { background: #D4A01710; color: var(--accent); font-size: 12px; padding: 2px 8px; border-radius: 12px; }

  .detail-section { margin-bottom: 24px; }
  .detail-section h3 { font-size: 14px; font-weight: 600; margin-bottom: 10px; color: var(--text); }

  .review-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px;
    margin-bottom: 8px;
    box-shadow: var(--shadow-sm);
  }
  .review-score { color: var(--orange); font-weight: 600; margin-bottom: 4px; }
  .review-form { margin-top: 12px; }
  .review-stars { display: flex; gap: 4px; margin-bottom: 8px; }
  .review-stars span {
    font-size: 22px; cursor: pointer; color: var(--border); transition: color 0.1s; user-select: none;
  }
  .review-stars span.active { color: var(--orange); }
  .review-stars:hover span { color: var(--orange); }
  .review-stars span:hover ~ span { color: var(--border); }
  .review-form textarea {
    width: 100%; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 8px 10px; color: var(--text); font-size: 13px; font-family: inherit; resize: none;
    min-height: 38px; outline: none; overflow: hidden;
  }
  .review-form textarea:focus { border-color: var(--accent); }
  .review-form button {
    margin-top: 8px; background: var(--accent); color: #fff; border: none; border-radius: var(--radius);
    padding: 6px 16px; font-size: 13px; font-weight: 500; cursor: pointer;
  }
  .review-form button:hover { opacity: 0.9; }
  .review-form button:disabled { opacity: 0.5; cursor: default; }
  .review-comment { font-size: 13px; color: var(--muted); }

  /* ── Empty / loading ── */
  .empty { text-align: center; padding: 60px 0; color: var(--muted); }
  .empty-title { font-size: 16px; margin-bottom: 6px; color: var(--text); }
  .empty-sub { font-size: 13px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner { width: 20px; height: 20px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite; margin: 0 auto 12px; }

  .results-count { font-size: 13px; color: var(--muted); margin-bottom: 12px; }

  /* ── User cards ── */
  .user-card {
    display: flex; align-items: center; gap: 14px;
    padding: 14px 16px; border-radius: var(--radius);
    cursor: pointer; transition: all 0.15s;
    border: 1px solid transparent;
  }
  .user-card:hover { background: var(--surface); border-color: var(--border); box-shadow: var(--shadow); }
  .user-card-avatar {
    width: 40px; height: 40px; border-radius: 50%;
    background: linear-gradient(135deg, #FFEA0020, #FF950020);
    border: 2px solid #FFC30040;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; font-weight: 600; color: var(--accent-deep); flex-shrink: 0;
  }
  .user-card-info { flex: 1; min-width: 0; }
  .user-card-handle { font-size: 15px; font-weight: 600; color: var(--text); }
  .user-card-meta { font-size: 13px; color: var(--muted); }

  /* ── File explorer (sidebar + viewer) ── */
  .file-explorer {
    display: grid;
    grid-template-columns: 240px 1fr;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    box-shadow: var(--shadow);
    height: calc(100vh - 200px);
    min-height: 400px;
  }
  .file-tree {
    background: var(--surface2);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    padding: 8px 0;
    font-size: 13px;
  }
  .file-tree-group { margin-bottom: 2px; }
  .file-tree-folder {
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.3px;
    cursor: pointer;
    user-select: none;
    display: flex; align-items: center; gap: 4px;
  }
  .file-tree-folder:hover { color: var(--text); }
  .file-tree-folder .chevron { font-size: 10px; transition: transform 0.15s; display: inline-block; }
  .file-tree-folder.collapsed .chevron { transform: rotate(-90deg); }
  .file-tree-folder.collapsed + .file-tree-items { display: none; }
  .file-tree-item {
    padding: 4px 12px 4px 28px;
    cursor: pointer;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-family: "SF Mono", "Fira Code", monospace;
    font-size: 12px;
    transition: background 0.1s;
  }
  .file-tree-item:hover { background: var(--border); }
  .file-tree-item.active { background: #D4A01712; color: var(--accent); font-weight: 500; }
  .file-tree-item.too-large { color: var(--muted); font-style: italic; }

  .file-viewer {
    overflow-y: auto;
    overflow-x: auto;
    background: var(--surface);
  }
  .file-viewer-header {
    background: var(--surface2);
    border-bottom: 1px solid var(--border);
    padding: 8px 14px;
    font-size: 13px;
    font-family: "SF Mono", "Fira Code", monospace;
    color: var(--muted);
    display: flex; align-items: center; gap: 6px;
    position: sticky; top: 0; z-index: 1;
  }
  .file-viewer-header .fname { color: var(--text); font-weight: 500; }
  pre.code-block {
    background: var(--surface);
    padding: 0;
    margin: 0;
    overflow-x: auto;
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 13px;
    line-height: 1.55;
    color: var(--text);
    tab-size: 4;
    display: grid;
    grid-template-columns: auto 1fr;
  }
  pre.code-block code { background: none !important; padding: 12px 16px 12px 0 !important; font-size: inherit; line-height: inherit; font-family: inherit; display: block; min-width: 0; }
  .line-numbers {
    padding: 12px 12px 12px 16px;
    text-align: right;
    color: var(--muted);
    user-select: none;
    border-right: 1px solid var(--border);
    font-family: inherit;
    font-size: inherit;
    line-height: inherit;
    white-space: pre;
  }

  .file-too-large {
    padding: 40px 20px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
  }
  .file-too-large .size { font-weight: 600; color: var(--text); }

  /* Files section full-width below the grid */
  .detail-files-full {
    margin-top: 24px;
  }


  /* ── Profile header ── */
  .profile-header {
    display: flex;
    align-items: center;
    gap: 18px;
    margin-bottom: 28px;
    padding: 20px 24px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: var(--shadow);
  }
  .profile-avatar {
    width: 56px; height: 56px;
    border-radius: 50%;
    background: linear-gradient(135deg, #FFEA0020, #FF950020);
    border: 2px solid #FFC30040;
    display: flex; align-items: center; justify-content: center;
    font-size: 24px; font-weight: 600; color: var(--accent-deep);
    flex-shrink: 0;
  }
  .profile-info { flex: 1; min-width: 0; }
  .profile-name { font-size: 20px; font-weight: 600; margin-bottom: 2px; }
  .profile-handle { font-size: 14px; color: var(--muted); }
  .profile-stats {
    display: flex; gap: 20px; margin-top: 6px;
    font-size: 13px; color: var(--muted);
  }
  .profile-stat strong { color: var(--text); font-weight: 600; }

  /* ── Responsive (tablet-ish, not mobile) ── */
  @media (max-width: 900px) {
    .content { padding: 20px; }
  }
</style>
</head>
<body>

<nav>
  <span class="nav-logo" onclick="navigate('profile')"><span class="nav-logo-mark"><svg viewBox="0 0 200 200"><defs><linearGradient id="cg" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#FFFFFF"/><stop offset="50%" stop-color="#E4E4E7"/><stop offset="100%" stop-color="#A1A1AA"/></linearGradient></defs><path d="M 125,45 A 60 60 0 1 0 125,155" fill="none" stroke="url(#cg)" stroke-width="24" stroke-linecap="round"/><rect x="88" y="88" width="24" height="24" rx="4" fill="#FFEA00"/><rect x="135" y="92" width="16" height="16" rx="3" fill="#FFC300"/><rect x="175" y="95" width="10" height="10" rx="2" fill="#FF9500"/></svg></span>Cartograph</span>
  <div class="nav-tabs">
    <button class="nav-tab active" data-view="profile">Profile <span class="tab-badge" id="badge-my">0</span></button>
    <button class="nav-tab" data-view="search">Search</button>
  </div>
  <div class="nav-right">
    <button class="btn" id="theme-btn" title="Toggle dark mode"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg></button>
    <button class="btn" id="refresh-btn" title="Refresh data">&#8635; Refresh</button>
  </div>
</nav>

<div class="content" id="content">
  <div class="empty"><div class="spinner"></div>Loading...</div>
</div>

<script>
// ── State ──
let currentView = 'profile';
let searchQuery = '';
let searchResults = [];
let searchDomains = [];
let searchLangs = [];
let myWidgets = [];
let whoamiData = {};
let detailWidget = null;
let isSearching = false;
let userSearchResults = [];
let isUserSearch = false;
let viewingProfile = null;  // null = own profile, string = other user's handle

// ── Language helpers ──
function langClass(lang) {
  const l = (lang||'unknown').toLowerCase();
  if (l === 'python') return 'lang-python';
  if (l === 'javascript' || l === 'js') return 'lang-javascript';
  if (l === 'typescript' || l === 'ts') return 'lang-typescript';
  if (l === 'nim') return 'lang-nim';
  if (l === 'angular' || l === 'ang' || l === 'ng') return 'lang-angular';
  if (l === 'php') return 'lang-php';
  if (l === 'openscad' || l === 'scad') return 'lang-openscad';
  if (l === 'systemverilog' || l === 'sv') return 'lang-systemverilog';
  if (l === 'terraform' || l === 'tf') return 'lang-terraform';
  if (l === 'go' || l === 'golang') return 'lang-go';
  if (l === 'spice' || l === 'cir') return 'lang-spice';
  if (l === 'rust' || l === 'rs') return 'lang-rust';
  if (l === 'gdscript' || l === 'gd' || l === 'godot') return 'lang-gdscript';
  if (l === 'java' || l === 'jdk' || l === 'openjdk') return 'lang-java';
  if (l === 'lean' || l === 'lean4') return 'lang-lean';
  if (l === 'csharp' || l === 'cs' || l === 'c#' || l === 'dotnet') return 'lang-csharp';
  if (l === 'flutter') return 'lang-flutter';
  return 'lang-unknown';
}

function stars(n) {
  if (!n) return '';
  const full = Math.floor(n);
  let s = '';
  for (let i = 0; i < full; i++) s += '&#9733;';
  if (n - full >= 0.5) s += '&#9734;';
  return s;
}

// ── Navigation ──
function navigate(view, data) {
  if (view === 'detail') {
    detailWidget = data;
    detailFiles = null;
    detailVersions = [];
    detailChangelog = {};
    detailCloudReviews = [];
    detailViewingVersion = '';
    const wid = data.id || data.name;
    if (wid) {
      loadDetailFiles(wid, data.owner || '');
      loadDetailVersions(wid);
      if (data.owner) loadCloudReviews(data.owner, wid);
    }
  } else if (view === 'user') {
    // Viewing another user's profile
    viewingProfile = data;
    detailWidget = null;
    detailFiles = null;
    loadUserWidgets(data);
    currentView = 'profile';
  } else {
    if (view === 'profile') viewingProfile = null;
    detailWidget = null;
    detailFiles = null;
  }
  if (view !== 'user') currentView = view;

  // Update nav tabs
  const src = detailWidget ? (detailWidget._source || 'profile') : 'profile';
  const tabView = currentView === 'detail' ? src : currentView;
  document.querySelectorAll('.nav-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.view === tabView);
  });

  // Persist view in URL hash
  if (view === 'detail' && data) {
    location.hash = `#detail/${encodeURIComponent(data.id || data.name)}`;
  } else if (view === 'user' && data) {
    location.hash = `#user/${encodeURIComponent(data)}`;
  } else {
    location.hash = `#${view}`;
  }

  render();
}

// Load another user's public widgets
let userWidgets = [];
async function loadUserWidgets(owner) {
  userWidgets = [];
  render();
  try {
    const res = await fetch(`/api/search?q=*&owner=${encodeURIComponent(owner)}`);
    const data = await res.json();
    userWidgets = (data.widgets || []).map(w => ({
      ...w,
      id: w.id || (w.namespaced_id || '').replace(/^@[^/]+\//, ''),
    }));
  } catch {
    userWidgets = [];
  }
  render();
}

function viewUserProfile(owner) {
  navigate('user', owner);
}

// ── Tab clicks ──
document.querySelectorAll('.nav-tab').forEach(btn => {
  btn.addEventListener('click', () => navigate(btn.dataset.view));
});

// ── Theme toggle ──
document.getElementById('theme-btn').addEventListener('click', () => {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  html.setAttribute('data-theme', isDark ? '' : 'dark');
  document.getElementById('theme-btn').innerHTML = isDark
    ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'
    : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
  // Swap highlight.js theme
  const hljsLink = document.querySelector('link[href*="highlight.js"]');
  if (hljsLink) {
    hljsLink.href = isDark
      ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css'
      : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';
  }
  localStorage.setItem('carto-theme', isDark ? 'light' : 'dark');
});

// Restore saved theme
(function() {
  const saved = localStorage.getItem('carto-theme');
  if (saved === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
    document.getElementById('theme-btn').innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
    const hljsLink = document.querySelector('link[href*="highlight.js"]');
    if (hljsLink) hljsLink.href = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';
  }
})();

// ── Refresh ──
document.getElementById('refresh-btn').addEventListener('click', async () => {
  const btn = document.getElementById('refresh-btn');
  btn.style.pointerEvents = 'none';
  btn.textContent = '... Refreshing';
  const reloadRes = await fetch('/api/reload').then(r => r.json()).catch(e => ({error: String(e)}));
  console.log('[refresh] reload:', reloadRes);
  await loadMyWidgets();
  if (searchQuery) await doSearch();
  btn.innerHTML = '&#8635; Refresh';
  btn.style.pointerEvents = '';
  render();
});

// ── Search ──
async function doSearch() {
  isSearching = true;
  isUserSearch = searchQuery.startsWith('@');
  render();

  if (isUserSearch) {
    const q = searchQuery.slice(1);
    try {
      const res = await fetch('/api/users/search?q=' + encodeURIComponent(q));
      const data = await res.json();
      userSearchResults = data.users || [];
    } catch {
      userSearchResults = [];
    }
    searchResults = [];
  } else {
    const params = new URLSearchParams({ q: searchQuery });
    try {
      const [cloudRes, localRes, usersRes] = await Promise.all([
        fetch('/api/search?' + params).then(r => r.json()).catch(() => ({widgets:[]})),
        fetch('/api/search-local?' + params).then(r => r.json()).catch(() => ({widgets:[]})),
        fetch('/api/users/search?q=' + encodeURIComponent(searchQuery)).then(r => r.json()).catch(() => ({users:[]})),
      ]);
      const seen = new Set();
      const merged = [];
      for (const w of (localRes.widgets || [])) {
        if (!seen.has(w.id)) { seen.add(w.id); merged.push(w); }
      }
      for (const w of (cloudRes.widgets || [])) {
        const baseId = w.id || (w.namespaced_id || '').replace(/^@[^/]+\//, '');
        if (!seen.has(baseId)) { seen.add(baseId); merged.push({...w, id: baseId}); }
      }
      searchResults = merged;
      userSearchResults = (usersRes.users || []).slice(0, 5);
    } catch {
      searchResults = [];
      userSearchResults = [];
    }
  }
  isSearching = false;
  render();
}

// ── Widget card HTML ──
function widgetCard(w, opts = {}) {
  const lang = (w.language || 'unknown').toLowerCase();
  const owner = w.owner || '';
  const ver = w.version || w._localVersion || w._cloudVersion || '';
  const installs = w.install_count || 0;
  const vis = w.visibility || 'public';

  let badges = '';
  if (opts.showSync && w._sync) {
    const syncLabels = {
      local: 'Local only', cloud: 'Cloud only', published: 'Published',
      mismatch: `v${w._localVersion||'?'} &#8594; v${w._cloudVersion||'?'}`
    };
    const syncClasses = { local: 'sync-local', cloud: 'sync-cloud', published: 'sync-published', mismatch: 'sync-mismatch' };
    badges += `<span class="sync-badge ${syncClasses[w._sync]}">${syncLabels[w._sync]}</span>`;
  }
  if (vis === 'private') badges += `<span class="vis-badge vis-private">private</span>`;

  return `<div class="widget-card" onclick="viewDetail(${JSON.stringify(w).replace(/"/g,'&quot;').replace(/'/g,'&#39;')}, '${opts.source||'library'}')">
    <div class="widget-header">
      <span class="widget-name">${coloredName(w.id || w.name, w.language)}</span>
      ${owner ? `<span class="widget-owner" onclick="event.stopPropagation();viewUserProfile('${esc(owner)}')" style="cursor:pointer">@${esc(owner)}</span>` : ''}
      ${ver ? `<span class="widget-version">v${esc(ver)}</span>` : ''}
      ${badges}
    </div>
    ${w.description ? `<div class="widget-desc">${esc(w.description)}</div>` : ''}
    <div class="widget-meta">
      <span><span class="lang-dot ${langClass(lang)}"></span> ${esc(lang)}</span>
      ${w.domain ? `<span class="domain-tag">${esc(w.domain)}</span>` : ''}
      ${installs ? `<span>${installs} install${installs!==1?'s':''}</span>` : ''}
      ${w._cloudRating ? `<span title="Cloud rating">${stars(w._cloudRating)} ${w._cloudRating.toFixed(1)} <span style="color:var(--muted)">(${w._cloudReviewCount||0})</span></span>` : w._localRating ? `<span title="Local rating">${stars(w._localRating)} ${w._localRating.toFixed(1)} <span style="color:var(--muted)">(${w._localReviewCount||0})</span></span>` : ''}
    </div>
  </div>`;
}

function coloredName(id, lang) {
  const name = id || '?';
  const l = (lang||'').toLowerCase();
  const suffix = '-' + l;
  if (l && name.endsWith(suffix)) {
    const base = name.slice(0, -suffix.length);
    const cls = langClass(l).replace('lang-', 'lang-text-');
    return `${esc(base)}-<span class="${cls}">${esc(l)}</span>`;
  }
  return esc(name);
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function viewDetail(w, source) {
  w._source = source;
  navigate('detail', w);
}

// ── Render ──
function render() {
  const el = document.getElementById('content');
  const viewClass = (currentView === 'detail' && detailWidget) ? 'detail' : currentView;
  el.className = 'content view-' + viewClass;

  if (currentView === 'detail' && detailWidget) {
    el.innerHTML = renderDetail(detailWidget);
    if (typeof hljs !== 'undefined') hljs.highlightAll();
    return;
  }

  if (currentView === 'profile') {
    el.innerHTML = renderProfile();
    bindProfileEvents();
    return;
  }

  if (currentView === 'search') {
    el.innerHTML = renderSearch();
    bindSearchEvents();
    return;
  }
}

// ── Profile view (home) ──
function renderProfile() {
  const isOwnProfile = !viewingProfile;
  const widgets = isOwnProfile ? myWidgets : userWidgets;
  const handle = isOwnProfile
    ? (whoamiData.owner || whoamiData.email || '')
    : viewingProfile;

  let html = '';

  // Profile header
  html += `<div class="profile-header">`;
  const initial = (handle || '?')[0].toUpperCase();
  html += `<div class="profile-avatar">${esc(initial)}</div>`;
  html += `<div class="profile-info">`;

  if (isOwnProfile && !whoamiData.authenticated) {
    html += `<div class="profile-name">Local Library</div>`;
    html += `<div class="profile-handle">Not signed in - run <code style="background:var(--surface2);padding:2px 6px;border-radius:4px;font-size:12px">cartograph login</code> to sync with cloud</div>`;
  } else if (isOwnProfile) {
    html += `<div class="profile-name">@${esc(handle)}</div>`;
    html += `<div class="profile-handle">Your widgets</div>`;
  } else {
    html += `<div class="profile-name">@${esc(handle)}</div>`;
    html += `<div class="profile-handle" style="cursor:pointer;color:var(--accent)" onclick="navigate('profile')">&#8592; Back to your profile</div>`;
  }

  // Stats
  const localCount = widgets.filter(w => w._sync === 'local').length;
  const pubCount = widgets.filter(w => w._sync === 'published' || w._sync === 'mismatch').length;
  const cloudCount = widgets.filter(w => w._sync === 'cloud').length;
  const domains = [...new Set(widgets.map(w => w.domain).filter(Boolean))];

  html += `<div class="profile-stats">`;
  html += `<span><strong>${widgets.length}</strong> widget${widgets.length!==1?'s':''}</span>`;
  if (isOwnProfile && localCount) html += `<span><strong>${localCount}</strong> local</span>`;
  if (pubCount) html += `<span><strong>${pubCount}</strong> published</span>`;
  if (cloudCount) html += `<span><strong>${cloudCount}</strong> cloud-only</span>`;
  html += `<span><strong>${domains.length}</strong> domain${domains.length!==1?'s':''}</span>`;
  html += `</div>`;

  html += `</div>`; // profile-info
  html += `</div>`; // profile-header

  if (!widgets.length) {
    if (isOwnProfile) {
      html += `<div class="empty">
        <div class="empty-title">No widgets yet</div>
        <div class="empty-sub">Create one with <code>cartograph create</code> or search the registry to install one</div>
      </div>`;
    } else {
      html += `<div class="empty">
        <div class="empty-title">No public widgets</div>
        <div class="empty-sub">@${esc(handle)} hasn't published any widgets yet</div>
      </div>`;
    }
    return html;
  }

  const allDomains = [...new Set(widgets.map(w => w.domain).filter(Boolean))].sort();
  const allLangs = [...new Set(widgets.map(w => (w.language||'').toLowerCase()).filter(Boolean))].sort();

  // Filter bar
  html += `<div class="filter-bar">`;
  html += `<input type="search" id="profile-search" placeholder="Filter widgets..." />`;

  if (isOwnProfile) {
    html += `<div class="seg-control">
      <button class="seg-btn active" data-status="all">All ${widgets.length}</button>
      <button class="seg-btn" data-status="local">Local ${localCount}</button>
      <button class="seg-btn" data-status="published">Published ${pubCount}</button>
      ${cloudCount ? `<button class="seg-btn" data-status="cloud">Cloud ${cloudCount}</button>` : ''}
    </div>`;
  }

  if (allLangs.length > 1) {
    html += `<div class="multi-select" data-filter="lang">
      <button class="multi-select-btn">Language</button>
      <div class="multi-select-menu">
        ${allLangs.map(l => `<label class="multi-select-item"><input type="checkbox" value="${esc(l)}" /> ${esc(l)}</label>`).join('')}
        <div class="multi-select-clear">Clear</div>
      </div>
    </div>`;
  }

  if (allDomains.length > 1) {
    html += `<div class="multi-select" data-filter="domain">
      <button class="multi-select-btn">Domain</button>
      <div class="multi-select-menu">
        ${allDomains.map(d => `<label class="multi-select-item"><input type="checkbox" value="${esc(d)}" /> ${esc(d)}</label>`).join('')}
        <div class="multi-select-clear">Clear</div>
      </div>
    </div>`;
  }

  html += `</div>`;

  const showSync = isOwnProfile;
  html += `<div id="profile-list" class="widget-list">${widgets.map(w => widgetCard(w, {showSync, source:'profile'})).join('')}</div>`;

  return html;
}

function bindProfileEvents() {
  const search = document.getElementById('profile-search');
  if (search) {
    search.addEventListener('input', () => filterProfile());
  }

  // Segmented control (status)
  document.querySelectorAll('.seg-btn[data-status]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.seg-btn[data-status]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterProfile();
    });
  });

  // Multi-select dropdowns
  document.querySelectorAll('.multi-select').forEach(ms => {
    const btn = ms.querySelector('.multi-select-btn');
    const menu = ms.querySelector('.multi-select-menu');
    const filterType = ms.dataset.filter;

    menu.addEventListener('click', (e) => e.stopPropagation());

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      // Close other open dropdowns
      document.querySelectorAll('.multi-select.open').forEach(other => {
        if (other !== ms) other.classList.remove('open');
      });
      ms.classList.toggle('open');
    });

    menu.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        updateMultiSelectLabel(ms, filterType);
        filterProfile();
      });
    });

    menu.querySelector('.multi-select-clear').addEventListener('click', () => {
      menu.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
      updateMultiSelectLabel(ms, filterType);
      filterProfile();
    });
  });

  // Close dropdowns on outside click
  document.addEventListener('click', () => {
    document.querySelectorAll('.multi-select.open').forEach(ms => ms.classList.remove('open'));
  });
}

function updateMultiSelectLabel(ms, filterType) {
  const checked = [...ms.querySelectorAll('input[type="checkbox"]:checked')].map(cb => cb.value);
  const btn = ms.querySelector('.multi-select-btn');
  const label = filterType === 'lang' ? 'Language' : 'Domain';
  if (checked.length === 0) {
    btn.textContent = label;
    btn.classList.remove('has-selection');
  } else {
    btn.textContent = `${label} (${checked.length})`;
    btn.classList.add('has-selection');
  }
}

function getMultiSelectValues(filterType) {
  const ms = document.querySelector(`.multi-select[data-filter="${filterType}"]`);
  if (!ms) return [];
  return [...ms.querySelectorAll('input[type="checkbox"]:checked')].map(cb => cb.value);
}

function filterProfile() {
  const isOwnProfile = !viewingProfile;
  const widgets = isOwnProfile ? myWidgets : userWidgets;
  const q = (document.getElementById('profile-search')?.value || '').toLowerCase();
  const statusFilter = document.querySelector('.seg-btn[data-status].active')?.dataset.status || 'all';
  const selectedLangs = getMultiSelectValues('lang');
  const selectedDomains = getMultiSelectValues('domain');

  let filtered = widgets;
  if (isOwnProfile) {
    if (statusFilter === 'local') filtered = filtered.filter(w => w._sync === 'local');
    else if (statusFilter === 'published') filtered = filtered.filter(w => w._sync === 'published' || w._sync === 'mismatch');
    else if (statusFilter === 'cloud') filtered = filtered.filter(w => w._sync === 'cloud');
  }

  if (selectedLangs.length) filtered = filtered.filter(w => selectedLangs.includes((w.language||'').toLowerCase()));
  if (selectedDomains.length) filtered = filtered.filter(w => selectedDomains.includes(w.domain));

  if (q) {
    filtered = filtered.filter(w =>
      (w.id||'').toLowerCase().includes(q) ||
      (w.description||'').toLowerCase().includes(q) ||
      (w.domain||'').toLowerCase().includes(q)
    );
  }

  const listEl = document.getElementById('profile-list');
  if (listEl) {
    const showSync = isOwnProfile;
    listEl.innerHTML = filtered.length
      ? filtered.map(w => widgetCard(w, {showSync, source:'profile'})).join('')
      : `<div class="empty"><div class="empty-title">No matches</div></div>`;
  }
}

// ── Search view ──
function renderSearch() {
  let html = '';

  html += `<div class="search-bar">
    <span class="search-bar-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></span>
    <input type="search" id="search-input" placeholder="Search widgets or @username..." value="${esc(searchQuery)}" />
  </div>`;

  if (!searchQuery) {
    html += `<div class="empty">
      <div class="empty-sub">Search widgets by keyword, or type <strong>@handle</strong> to find a user</div>
    </div>`;
    return html;
  }

  if (isSearching) {
    html += `<div class="empty"><div class="spinner"></div>Searching...</div>`;
    return html;
  }

  if (isUserSearch) {
    html += `<div class="results-count">${userSearchResults.length} user${userSearchResults.length!==1?'s':''} matching "${esc(searchQuery)}"</div>`;
    if (!userSearchResults.length) {
      html += `<div class="empty"><div class="empty-title">No users found</div><div class="empty-sub">Try a different handle</div></div>`;
    } else {
      html += `<div class="widget-list">`;
      userSearchResults.forEach(u => {
        const handle = u.handle || u.owner || u.username || '';
        const initial = (handle || '?')[0].toUpperCase();
        const widgetCount = u.widget_count || u.widgets || '';
        html += `<div class="user-card" onclick="viewUserProfile('${esc(handle)}')">
          <div class="user-card-avatar">${esc(initial)}</div>
          <div class="user-card-info">
            <div class="user-card-handle">@${esc(handle)}</div>
            ${widgetCount ? `<div class="user-card-meta">${widgetCount} widget${widgetCount!==1?'s':''}</div>` : ''}
          </div>
        </div>`;
      });
      html += `</div>`;
    }
    return html;
  }

  // Users section (shown above widgets in general search)
  if (userSearchResults.length) {
    html += `<div style="margin-bottom:20px">
      <h3 style="font-size:12px;text-transform:uppercase;color:var(--muted);letter-spacing:0.5px;margin-bottom:10px">Users</h3>
      <div class="widget-list">`;
    userSearchResults.forEach(u => {
      const handle = u.handle || u.owner || u.username || '';
      const initial = (handle || '?')[0].toUpperCase();
      const widgetCount = u.widget_count || u.widgets || '';
      html += `<div class="user-card" onclick="viewUserProfile('${esc(handle)}')">
        <div class="user-card-avatar">${esc(initial)}</div>
        <div class="user-card-info">
          <div class="user-card-handle">@${esc(handle)}</div>
          ${widgetCount ? `<div class="user-card-meta">${widgetCount} widget${widgetCount!==1?'s':''}</div>` : ''}
        </div>
      </div>`;
    });
    html += `</div></div>`;
  }

  const totalResults = searchResults.length + userSearchResults.length;
  html += `<div class="results-count">${searchResults.length} widget${searchResults.length!==1?'s':''} for "${esc(searchQuery)}"</div>`;

  if (!searchResults.length && !userSearchResults.length) {
    html += `<div class="empty"><div class="empty-title">No results</div><div class="empty-sub">Try different keywords or check your spelling</div></div>`;
  } else if (searchResults.length) {
    // Filters on results
    const domains = [...new Set(searchResults.map(w => w.domain).filter(Boolean))].sort();
    const langs = [...new Set(searchResults.map(w => (w.language||'').toLowerCase()).filter(Boolean))].sort();
    if (domains.length > 1 || langs.length > 1) {
      html += `<div class="filter-bar" style="margin-top:12px">`;
      if (langs.length > 1) {
        html += `<div class="multi-select" data-filter="search-lang">
          <button class="multi-select-btn${searchLangs.length ? ' has-selection' : ''}">Language${searchLangs.length ? ' (' + searchLangs.length + ')' : ''}</button>
          <div class="multi-select-menu">
            ${langs.map(l => `<label class="multi-select-item"><input type="checkbox" value="${esc(l)}"${searchLangs.includes(l) ? ' checked' : ''} /> ${esc(l)}</label>`).join('')}
            <div class="multi-select-clear">Clear</div>
          </div>
        </div>`;
      }
      if (domains.length > 1) {
        html += `<div class="multi-select" data-filter="search-domain">
          <button class="multi-select-btn${searchDomains.length ? ' has-selection' : ''}">Domain${searchDomains.length ? ' (' + searchDomains.length + ')' : ''}</button>
          <div class="multi-select-menu">
            ${domains.map(d => `<label class="multi-select-item"><input type="checkbox" value="${esc(d)}"${searchDomains.includes(d) ? ' checked' : ''} /> ${esc(d)}</label>`).join('')}
            <div class="multi-select-clear">Clear</div>
          </div>
        </div>`;
      }
      html += `</div>`;
    }

    let filtered = searchResults;
    if (searchLangs.length) filtered = filtered.filter(w => searchLangs.includes((w.language||'').toLowerCase()));
    if (searchDomains.length) filtered = filtered.filter(w => searchDomains.includes(w.domain));
    html += `<div class="widget-list">${filtered.map(w => widgetCard(w, {source:'search'})).join('')}</div>`;
  }

  return html;
}

function bindSearchEvents() {
  const input = document.getElementById('search-input');
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && e.target.value.trim()) {
        searchQuery = e.target.value.trim();
        searchLangs = [];
        searchDomains = [];
        doSearch();
      }
    });
    input.focus();
  }

  // Multi-select dropdowns in search results
  document.querySelectorAll('.multi-select[data-filter^="search-"]').forEach(ms => {
    const btn = ms.querySelector('.multi-select-btn');
    const filterType = ms.dataset.filter;

    ms.querySelector('.multi-select-menu')?.addEventListener('click', (e) => e.stopPropagation());

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.multi-select.open').forEach(other => {
        if (other !== ms) other.classList.remove('open');
      });
      ms.classList.toggle('open');
    });

    ms.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        if (filterType === 'search-lang') searchLangs = [...ms.querySelectorAll('input:checked')].map(c => c.value);
        if (filterType === 'search-domain') searchDomains = [...ms.querySelectorAll('input:checked')].map(c => c.value);
        render();
      });
    });

    ms.querySelector('.multi-select-clear')?.addEventListener('click', () => {
      ms.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
      if (filterType === 'search-lang') searchLangs = [];
      if (filterType === 'search-domain') searchDomains = [];
      render();
    });
  });
}

// ── Detail view ──
let detailFiles = null;
let detailVersions = [];
let detailChangelog = {};
let detailCloudReviews = [];
let detailViewingVersion = '';

function renderFilesExplorer() {
  if (!detailFiles || !Object.keys(detailFiles).length) return '';

  const fileNames = Object.keys(detailFiles).sort((a, b) => {
    const order = f => f.startsWith('src/') ? 0 : f.startsWith('tests/') ? 1 : f.startsWith('examples/') ? 2 : f === 'widget.json' ? 3 : 4;
    return order(a) - order(b) || a.localeCompare(b);
  });

  const groups = {};
  const rootFiles = [];
  fileNames.forEach(f => {
    const slash = f.indexOf('/');
    if (slash === -1) { rootFiles.push(f); }
    else {
      const folder = f.substring(0, slash);
      if (!groups[folder]) groups[folder] = [];
      groups[folder].push(f);
    }
  });

  const firstFile = fileNames[0];
  const isTooLarge = (content) => content && content.startsWith('[File too large:');

  let html = `<h3 style="margin-bottom:12px">Files (${fileNames.length})${detailViewingVersion ? ` <span style="font-weight:400;color:var(--muted);font-size:12px">- v${esc(detailViewingVersion)}</span>` : ''}</h3>`;
  html += `<div class="file-explorer">`;

  html += `<div class="file-tree">`;
  const folderOrder = ['src', 'tests', 'examples'];
  const sortedFolders = Object.keys(groups).sort((a, b) => {
    const ai = folderOrder.indexOf(a), bi = folderOrder.indexOf(b);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    return a.localeCompare(b);
  });

  sortedFolders.forEach(folder => {
    html += `<div class="file-tree-group">`;
    html += `<div class="file-tree-folder" onclick="this.classList.toggle('collapsed')"><span class="chevron">&#9662;</span> ${esc(folder)}/</div>`;
    html += `<div class="file-tree-items">`;
    groups[folder].forEach(f => {
      const name = f.substring(f.indexOf('/') + 1);
      const tooLarge = isTooLarge(detailFiles[f]);
      html += `<div class="file-tree-item${f === firstFile ? ' active' : ''}${tooLarge ? ' too-large' : ''}" data-file="${esc(f)}" onclick="selectFile(this, '${esc(f)}')">${esc(name)}</div>`;
    });
    html += `</div></div>`;
  });

  if (rootFiles.length) {
    rootFiles.forEach(f => {
      const tooLarge = isTooLarge(detailFiles[f]);
      html += `<div class="file-tree-item${f === firstFile ? ' active' : ''}${tooLarge ? ' too-large' : ''}" data-file="${esc(f)}" onclick="selectFile(this, '${esc(f)}')" style="padding-left:12px">${esc(f)}</div>`;
    });
  }
  html += `</div>`;

  const firstContent = detailFiles[firstFile] || '';
  const firstLang = fileToLang(firstFile);
  html += `<div class="file-viewer" id="file-viewer">`;
  html += `<div class="file-viewer-header"><span class="fname">${esc(firstFile)}</span></div>`;
  if (isTooLarge(firstContent)) {
    const sizeMatch = firstContent.match(/\\d+/);
    const sizeKB = sizeMatch ? (parseInt(sizeMatch[0]) / 1024).toFixed(0) : '?';
    html += `<div class="file-too-large">File too large to preview<br><span class="size">${sizeKB} KB</span></div>`;
  } else {
    html += codeWithLines(firstContent, firstLang);
  }
  html += `</div>`;
  html += `</div>`;

  return html;
}

function renderDetail(w) {
  const lang = (w.language || 'unknown').toLowerCase();
  const owner = w.owner || '';
  const ver = w.version || w._localVersion || w._cloudVersion || '';
  const installs = w.install_count || 0;
  const vis = w.visibility || '';
  const sync = w._sync || '';
  const tags = w.tags || [];
  const deps = (w.dependencies || (w.tech_stack && w.tech_stack.dependencies) || []);
  const reviews = w.reviews || [];
  const nsid = w.namespaced_id || (owner ? `@${owner}/${w.id}` : w.id);
  const notes = w.library_notes || {};

  // Status badge: local, published, cloud-only, or visibility from cloud
  let statusBadge = '';
  if (sync === 'local') statusBadge = '<span class="vis-badge vis-local">local</span>';
  else if (sync === 'published') statusBadge = '<span class="vis-badge vis-public">published</span>';
  else if (sync === 'mismatch') statusBadge = '<span class="vis-badge vis-mismatch">out of sync</span>';
  else if (sync === 'cloud') statusBadge = '<span class="vis-badge vis-cloud">cloud</span>';
  else if (vis === 'private') statusBadge = '<span class="vis-badge vis-private">private</span>';
  else if (vis) statusBadge = `<span class="vis-badge vis-public">${esc(vis)}</span>`;

  let html = `<div class="detail-back" onclick="navigate('${w._source||'profile'}')">&#8592; Back</div>`;

  html += `<div class="detail-title">
    <span>${coloredName(w.id || w.name, w.language)}</span>
    ${statusBadge}
  </div>`;
  if (owner) html += `<div class="detail-owner">by <span style="cursor:pointer;color:var(--accent)" onclick="viewUserProfile('${esc(owner)}')">@${esc(owner)}</span></div>`;

  const ratingParts = [];
  if (w._localRating) ratingParts.push(`${stars(w._localRating)} ${w._localRating.toFixed(1)} local (${w._localReviewCount||0})`);
  if (w._cloudRating) ratingParts.push(`${stars(w._cloudRating)} ${w._cloudRating.toFixed(1)} cloud (${w._cloudReviewCount||0})`);
  if (ratingParts.length) html += `<div style="font-size:14px;color:var(--muted);margin-bottom:16px">${ratingParts.join('<span style="margin:0 10px;color:var(--border)">|</span>')}</div>`;

  html += `<div class="detail-grid">`;

  // Main content
  html += `<div class="detail-main">`;
  if (w.description) html += `<div class="detail-desc">${esc(w.description)}</div>`;

  // Install command
  html += `<div class="detail-section">
    <h3>Install</h3>
    <div class="install-cmd" onclick="copyInstall(this)" title="Click to copy">
      cartograph install ${esc(nsid)}
      <span class="copy-hint">click to copy</span>
    </div>
  </div>`;

  // Changelog (latest 3)
  const clEntries = Object.values(detailChangelog).sort((a,b) => b.version.localeCompare(a.version, undefined, {numeric:true})).slice(0,3);
  if (clEntries.length) {
    html += `<div class="detail-section">
      <h3>Changelog</h3>
      ${clEntries.map(e => `<div style="margin-bottom:8px">
        <div style="font-size:12px;font-weight:600">v${esc(e.version)}</div>
        <div style="font-size:12px;color:var(--muted);line-height:1.4">${esc(e.reason)}</div>
      </div>`).join('')}
    </div>`;
  }

  // Tags
  if (tags.length) {
    html += `<div class="detail-section">
      <h3>Tags</h3>
      <div class="tags-list">${tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
    </div>`;
  }

  // Cloud reviews
  if (detailCloudReviews.length) {
    html += `<div class="detail-section">
      <h3>Cloud Reviews (${detailCloudReviews.length})</h3>
      ${detailCloudReviews.map(r => `<div class="review-card">
        <div class="review-score">${stars(r.rating || r.score)} ${(r.rating || r.score)}/5 <span style="color:var(--muted);font-weight:400;font-size:12px">by ${r.author ? '@' + esc(r.author) : 'Anonymous'}${r.version ? ' on v' + esc(r.version) : ''}</span></div>
        ${r.comment ? `<div class="review-comment">${esc(r.comment)}</div>` : ''}
      </div>`).join('')}
    </div>`;
  }

  // Local reviews
  if (reviews.length) {
    html += `<div class="detail-section">
      <h3>Local Reviews (${reviews.length})</h3>
      ${reviews.map((r, i) => {
        const mine = r.author && whoamiData.owner && r.author === whoamiData.owner;
        return `<div class="review-card">
          <div class="review-score">${stars(r.rating)} ${r.rating}/5 <span style="color:var(--muted);font-weight:400;font-size:12px">by ${r.author ? '@' + esc(r.author) : 'Anonymous'}${r.version ? ' on v' + esc(r.version) : ''}</span>${mine ? `<span style="float:right;cursor:pointer;color:var(--muted);font-size:12px" onclick="event.stopPropagation();deleteReview('${esc(w.id||w.name)}',${i})" title="Delete review">&#10005;</span>` : ''}</div>
          ${r.comment ? `<div class="review-comment">${esc(r.comment)}</div>` : ''}
        </div>`;
      }).join('')}
    </div>`;
  }

  // Review form
  const hasAnyReviews = reviews.length || detailCloudReviews.length;
  html += `<div class="detail-section">
    ${!hasAnyReviews ? '<h3>Reviews</h3>' : ''}
    <div class="review-form">
      <div class="review-stars" id="review-stars">
        ${[1,2,3,4,5].map(n => `<span data-score="${n}" onclick="setReviewScore(${n})">&#9733;</span>`).join('')}
      </div>
      <textarea id="review-comment" placeholder="Add a comment (optional)" oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'"></textarea>
      <button id="review-submit" disabled onclick="submitReview('${esc(w.id||w.name)}','${esc(owner)}')">Submit Review</button>
    </div>
  </div>`;

  html += `</div>`; // detail-main

  // Sidebar
  html += `<div class="detail-sidebar">
    <h3>Details</h3>
    <div class="detail-meta-row"><span class="detail-meta-label">Version</span><span class="detail-meta-value">${detailVersions.length ? `<select id="version-select" style="background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:2px 4px;font-size:12px;cursor:pointer" onchange="switchVersion('${esc(w.id||w.name)}','${esc(owner)}',this.value)">
      <option value=""${!detailViewingVersion?' selected':''}>v${esc(ver)} (latest)</option>
      ${detailVersions.map(v=>`<option value="${esc(v)}"${detailViewingVersion===v?' selected':''}>v${esc(v)}</option>`).join('')}
    </select>` : (esc(ver) || '—')}</span></div>
    <div class="detail-meta-row"><span class="detail-meta-label">Language</span><span class="detail-meta-value"><span class="lang-dot ${langClass(lang)}"></span> ${esc(lang)}</span></div>
    <div class="detail-meta-row"><span class="detail-meta-label">Domain</span><span class="detail-meta-value">${esc(w.domain || 'universal')}</span></div>
    <div class="detail-meta-row"><span class="detail-meta-label">Installs</span><span class="detail-meta-value">${installs}</span></div>
    <div class="detail-meta-row"><span class="detail-meta-label">Status</span><span class="detail-meta-value">${sync === 'local' ? 'Local only' : sync === 'published' ? 'Published' : sync === 'mismatch' ? 'Out of sync' : sync === 'cloud' ? 'Cloud only' : vis || 'Local only'}</span></div>
    ${owner ? `<div class="detail-meta-row"><span class="detail-meta-label">Owner</span><span class="detail-meta-value">@${esc(owner)}</span></div>` : ''}
    ${deps.length ? `<div class="detail-meta-row"><span class="detail-meta-label">Dependencies</span><span class="detail-meta-value">${deps.map(esc).join(', ')}</span></div>` : `<div class="detail-meta-row"><span class="detail-meta-label">Dependencies</span><span class="detail-meta-value">None</span></div>`}
    ${notes.general ? `<div style="margin-top:14px"><h3>Notes</h3><div style="font-size:12px;color:var(--muted);line-height:1.5;margin-top:6px">${esc(notes.general)}</div></div>` : ''}
  </div>`;

  html += `</div>`; // detail-grid

  // Source files — sidebar tree + viewer
  html += `<div class="detail-files-full" id="files-section">`;
  if (detailFiles && Object.keys(detailFiles).length) {
    html += renderFilesExplorer();
  } else if (detailFiles === null) {
    html += `<h3>Files</h3><div class="empty" style="padding:24px 0"><div class="spinner"></div>Loading source...</div>`;
  } else {
    html += `<h3>Files</h3><div style="color:var(--muted);font-size:13px;padding:12px 0">No source files available</div>`;
  }
  html += `</div>`;

  return html;
}

function fileToLang(filename) {
  const ext = filename.split('.').pop().toLowerCase();
  const map = {
    py: 'python', js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
    json: 'json', nim: 'nim', rs: 'rust', go: 'go', rb: 'ruby', sh: 'bash',
    md: 'markdown', yml: 'yaml', yaml: 'yaml', toml: 'ini',
    txt: 'plaintext', cfg: 'ini', ini: 'ini', html: 'xml', css: 'css',
  };
  return map[ext] || 'plaintext';
}

async function loadDetailFiles(widgetId, owner, version) {
  const isVersionSwitch = detailFiles !== null && version !== undefined;
  detailFiles = null;
  detailViewingVersion = version || '';

  if (isVersionSwitch) {
    updateFilesSection();
  } else {
    render();
  }

  try {
    let url = `/api/files/${encodeURIComponent(widgetId)}`;
    const params = [];
    if (owner) params.push(`owner=${encodeURIComponent(owner)}`);
    if (version) params.push(`version=${encodeURIComponent(version)}`);
    if (params.length) url += '?' + params.join('&');
    const res = await fetch(url);
    const data = await res.json();
    detailFiles = data.files || {};
  } catch {
    detailFiles = {};
  }

  if (isVersionSwitch) {
    updateFilesSection();
    updateVersionSidebar();
  } else {
    render();
  }
}

async function loadCloudReviews(owner, widgetId) {
  try {
    const res = await fetch(`/api/cloud-reviews/${encodeURIComponent(owner)}/${encodeURIComponent(widgetId)}`);
    const data = await res.json();
    detailCloudReviews = data.reviews || [];
  } catch {
    detailCloudReviews = [];
  }
  render();
}

async function loadDetailVersions(widgetId) {
  try {
    const res = await fetch(`/api/versions/${encodeURIComponent(widgetId)}`);
    const data = await res.json();
    detailVersions = data.versions || [];
    detailChangelog = {};
    (data.changelog || []).forEach(e => { detailChangelog[e.version] = e; });
  } catch {
    detailVersions = [];
    detailChangelog = {};
  }
  render();
}

function switchVersion(widgetId, owner, version) {
  loadDetailFiles(widgetId, owner, version);
}

function updateFilesSection() {
  const section = document.getElementById('files-section');
  if (!section) return;

  if (detailFiles && Object.keys(detailFiles).length) {
    section.innerHTML = renderFilesExplorer();
    if (typeof hljs !== 'undefined') hljs.highlightAll();
  } else if (detailFiles === null) {
    section.innerHTML = `<h3>Files</h3><div class="empty" style="padding:24px 0"><div class="spinner"></div>Loading source...</div>`;
  } else {
    section.innerHTML = `<h3>Files</h3><div style="color:var(--muted);font-size:13px;padding:12px 0">No source files available</div>`;
  }
}

let reviewScore = 0;
function setReviewScore(n) {
  reviewScore = n;
  document.querySelectorAll('#review-stars span').forEach(s => {
    s.classList.toggle('active', parseInt(s.dataset.score) <= n);
  });
  const btn = document.getElementById('review-submit');
  if (btn) btn.disabled = false;
}

async function submitReview(widgetId, owner) {
  if (!reviewScore) return;
  const comment = (document.getElementById('review-comment') || {}).value || '';
  const btn = document.getElementById('review-submit');
  if (btn) { btn.disabled = true; btn.textContent = 'Submitting...'; }
  try {
    const isCloud = !!owner;
    const url = isCloud ? '/api/cloud-review' : '/api/review';
    const payload = isCloud
      ? { owner, widget_id: widgetId, score: reviewScore, comment }
      : { widget_id: widgetId, score: reviewScore, comment };
    const res = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) { if (btn) { btn.disabled = false; btn.textContent = 'Submit Review'; } alert(data.error); return; }
    reviewScore = 0;
    const w = detailWidget;
    if (w) {
      if (isCloud) {
        detailCloudReviews.unshift({ rating: reviewScore || data.rating, comment, version: w.version, timestamp: new Date().toISOString(), author: data.author || whoamiData.owner || '' });
        w._cloudRating = data.avg_rating || w._cloudRating;
        w._cloudReviewCount = (w._cloudReviewCount || 0) + 1;
      } else {
        if (!w.reviews) w.reviews = [];
        w.reviews.unshift({ rating: data.rating, comment, version: w.version, timestamp: new Date().toISOString(), author: data.author || '' });
        w._localRating = data.avg_rating || w._localRating;
        w._localReviewCount = (w._localReviewCount || 0) + 1;
      }
    }
    render();
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = 'Submit Review'; }
  }
}

async function deleteReview(widgetId, index) {
  try {
    const res = await fetch('/api/review', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ widget_id: widgetId, index }),
    });
    const data = await res.json();
    if (data.error) return;
    if (detailWidget) {
      if (detailWidget.reviews) detailWidget.reviews.splice(index, 1);
      detailWidget._localRating = data.avg_rating || 0;
      detailWidget._localReviewCount = Math.max(0, (detailWidget._localReviewCount || 1) - 1);
    }
    render();
  } catch {}
}

function updateVersionSidebar() {
  const sel = document.getElementById('version-select');
  if (sel) sel.value = detailViewingVersion;
}

function lineNums(content) {
  const count = (content || '').split('\n').length;
  return Array.from({length: count}, (_, i) => i + 1).join('\n');
}

function codeWithLines(content, lang) {
  return `<pre class="code-block"><span class="line-numbers">${lineNums(content)}</span><code class="language-${lang}">${esc(content)}</code></pre>`;
}

function selectFile(el, filepath) {
  // Update active state in tree
  document.querySelectorAll('.file-tree-item').forEach(i => i.classList.remove('active'));
  el.classList.add('active');

  // Update viewer
  const viewer = document.getElementById('file-viewer');
  if (!viewer || !detailFiles) return;
  const content = detailFiles[filepath] || '';
  const lang = fileToLang(filepath);
  const isTooLarge = content.startsWith('[File too large:');

  let html = `<div class="file-viewer-header"><span class="fname">${esc(filepath)}</span></div>`;
  if (isTooLarge) {
    const sizeMatch = content.match(/\d+/);
    const sizeKB = sizeMatch ? (parseInt(sizeMatch[0]) / 1024).toFixed(0) : '?';
    html += `<div class="file-too-large">File too large to preview<br><span class="size">${sizeKB} KB</span></div>`;
  } else {
    html += codeWithLines(content, lang);
  }
  viewer.innerHTML = html;
  viewer.scrollTop = 0;
  if (typeof hljs !== 'undefined') hljs.highlightAll();
}

function copyInstall(el) {
  const text = el.textContent.replace('click to copy', '').trim();
  navigator.clipboard.writeText(text).then(() => {
    const hint = el.querySelector('.copy-hint');
    if (hint) { hint.textContent = 'copied!'; setTimeout(() => hint.textContent = 'click to copy', 1500); }
  });
}

// ── Data loading ──
async function loadMyWidgets() {
  const [whoami, localRes, cloudRes] = await Promise.all([
    fetch('/api/whoami').then(r => r.json()).catch(() => ({})),
    fetch('/api/local').then(r => r.json()).catch(() => ({widgets:[]})),
    fetch('/api/cloud').then(r => r.json()).catch(() => ({widgets:[]})),
  ]);

  whoamiData = whoami;

  // Merge local + cloud
  const map = {};
  (localRes.widgets || []).forEach(w => {
    map[w.id] = { ...w, _sync: 'local', _localVersion: w.version, _localRating: w.rating || 0, _localReviewCount: w.review_count || 0 };
  });
  (cloudRes.widgets || []).forEach(w => {
    const baseId = w.id || (w.namespaced_id || '').replace(/^@[^/]+\//, '');
    if (map[baseId]) {
      const lv = map[baseId]._localVersion;
      const cv = w.version;
      const local = map[baseId];
      map[baseId] = { ...local, ...w, id: baseId, version: lv };
      map[baseId]._cloudVersion = cv;
      map[baseId]._localVersion = lv;
      map[baseId]._localRating = local._localRating || 0;
      map[baseId]._localReviewCount = local._localReviewCount || 0;
      map[baseId]._cloudRating = w.rating || 0;
      map[baseId]._cloudReviewCount = w.review_count || 0;
      map[baseId]._sync = lv === cv ? 'published' : 'mismatch';
    } else {
      map[baseId] = { ...w, id: baseId, _sync: 'cloud', _cloudVersion: w.version, _cloudRating: w.rating || 0, _cloudReviewCount: w.review_count || 0 };
    }
  });

  myWidgets = Object.values(map).sort((a,b) => (a.id||'').localeCompare(b.id||''));
  document.getElementById('badge-my').textContent = myWidgets.length;
}

async function init() {
  await loadMyWidgets();

  // Restore view from URL hash
  const hash = location.hash.slice(1);
  if (hash.startsWith('detail/')) {
    const wid = decodeURIComponent(hash.slice(7));
    const w = myWidgets.find(w => w.id === wid);
    if (w) {
      navigate('detail', w);
      return;
    }
  } else if (hash.startsWith('user/')) {
    const owner = decodeURIComponent(hash.slice(5));
    navigate('user', owner);
    return;
  } else if (hash === 'search') {
    currentView = 'search';
  }

  render();
}

init();
</script>
</body>
</html>"""
