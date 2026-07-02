"""
report_builder.py — Generate a self-contained HTML report with interactive
                    Plotly charts embedded directly (no server required).

Public API
----------
generate_html_report(outdir, days) -> str
    outdir : directory where the .html file is written
    days   : list of {"label": str, "registry": [chart_dict, ...]}
             chart_dict keys: id, title, figure (go.Figure), source, method, export_hint
    returns: absolute path to the written HTML file
"""
from __future__ import annotations

import json
import math
import os
import urllib.parse

import plotly.graph_objects as go


def _json_safe(obj):
    """
    Recursively convert an object tree to JSON-serialisable types.
    Handles: numpy integers/floats/arrays/bools, pandas Timestamps,
             NaN/Inf floats, NaT.  Dict keys are coerced to str.
    """
    # --- numpy types ---
    try:
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return None if (np.isnan(obj) or np.isinf(obj)) else float(obj)
        if isinstance(obj, np.ndarray):
            return [_json_safe(v) for v in obj.tolist()]
        if isinstance(obj, np.bool_):
            return bool(obj)
    except ImportError:
        pass
    # --- plain float NaN / Inf ---
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    # --- dicts ---
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    # --- lists / tuples ---
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    # --- datetime-like (datetime, pd.Timestamp, date) ---
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    # --- pandas NA / NaT ---
    try:
        import pandas as pd
        if obj is pd.NaT or obj is pd.NA:
            return None
    except ImportError:
        pass
    return obj


# ── HTML skeleton ─────────────────────────────────────────────────────────────

_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hai Robotics ESS Log Analyzer</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  body  { font-family: 'Segoe UI', system-ui, Arial, sans-serif;
          background: #f1f5f9; color: #1a1a2e; margin: 0; padding: 0;
          -webkit-font-smoothing: antialiased; }

  /* --- header --- */
  header { background: #1a1a2e; color: #fff; padding: 30px 52px 26px; }
  header h1 { margin: 0 0 6px; font-size: 22px; font-weight: 700; letter-spacing: -.2px; }
  header p  { margin: 0; color: #64748b; font-size: 13px; }

  /* --- day tabs --- */
  .day-bar  { display: flex; gap: 0; flex-wrap: wrap;
              background: #fff; border-bottom: 1px solid #e2e8f0;
              padding: 0 48px; position: sticky; top: 0; z-index: 99;
              box-shadow: 0 1px 6px rgba(0,0,0,.07); }
  .day-btn  { padding: 13px 24px 14px; border: none; border-bottom: 2px solid transparent;
              margin-bottom: -1px; background: transparent; cursor: pointer;
              font-size: 13px; color: #64748b; font-family: inherit; font-weight: 500;
              transition: color .12s, background .12s; }
  .day-btn:hover   { color: #1a1a2e; background: #f8fafc; }
  .day-btn.active  { color: #1a1a2e; border-bottom-color: #e94560; font-weight: 700; }
  .day-panel       { display: none; }
  .day-panel.active { display: block; }

  /* --- layout --- */
  .container { max-width: 1440px; margin: 0 auto; padding: 36px 52px 64px; }

  /* --- chart cards --- */
  .card { background: #fff; border-radius: 10px;
          box-shadow: 0 1px 3px rgba(0,0,0,.06), 0 4px 16px rgba(0,0,0,.05);
          margin-bottom: 32px; overflow: hidden;
          border: 1px solid #e8edf3; }
  .card-meta { padding: 20px 28px 16px; border-bottom: 1px solid #f0f4f8; }
  .card-meta h2 { margin: 0 0 14px; font-size: 15px; font-weight: 700;
                  color: #0f172a; letter-spacing: -.1px; }
  .meta-row { display: flex; flex-wrap: wrap; gap: 4px 28px;
              font-size: 12px; line-height: 1.6; }
  .meta-item { display: flex; gap: 6px; align-items: baseline; }
  .lbl { font-weight: 600; color: #94a3b8; text-transform: uppercase;
         font-size: 10px; letter-spacing: .5px; white-space: nowrap; }
  .val { color: #334155; }
  .chart-wrap { padding: 4px 0 0; }

  /* --- export bar --- */
  .export-bar { display: flex; align-items: center; flex-wrap: wrap;
                gap: 8px; padding: 10px 28px 12px;
                background: #f8fafc; border-top: 1px solid #f0f4f8; }
  .export-label { font-size: 11px; font-weight: 600; color: #94a3b8;
                  text-transform: uppercase; letter-spacing: .5px; margin-right: 4px; }
  .export-link { display: inline-flex; align-items: center; gap: 5px;
                 color: #1d4ed8; text-decoration: none; font-size: 12px;
                 font-weight: 500; background: #eff6ff; border: 1px solid #bfdbfe;
                 border-radius: 5px; padding: 4px 10px;
                 transition: background .12s, border-color .12s; }
  .export-link:hover { background: #dbeafe; border-color: #93c5fd; }
  .export-link svg  { flex-shrink: 0; }

  /* --- section heading (multi-day) --- */
  .day-heading { font-size: 18px; font-weight: 700; color: #0f172a;
                 margin: 0 0 28px; padding-bottom: 14px;
                 border-bottom: 2px solid #e2e8f0; }

  footer { text-align: center; padding: 28px 0 20px;
           color: #94a3b8; font-size: 11px; letter-spacing: .2px; }

  .copy-btn { display: inline-flex; align-items: center; gap: 5px;
              color: #0f766e; font-size: 12px; font-weight: 500;
              background: #f0fdf4; border: 1px solid #a7f3d0;
              border-radius: 5px; padding: 4px 10px; cursor: pointer;
              font-family: inherit;
              transition: background .12s, border-color .12s; }
  .copy-btn:hover { background: #dcfce7; border-color: #6ee7b7; }
  .copy-btn-raw { display: inline-flex; align-items: center; gap: 5px;
              color: #92400e; font-size: 12px; font-weight: 500;
              background: #fffbeb; border: 1px solid #fcd34d;
              border-radius: 5px; padding: 4px 10px; cursor: pointer;
              font-family: inherit;
              transition: background .12s, border-color .12s; }
  .copy-btn-raw:hover { background: #fef3c7; border-color: #fbbf24; }

  /* --- methodology --- */
  .meth-section { margin-bottom: 36px; }
  .meth-section h2 { font-size: 18px; font-weight: 700; color: #0f172a;
                     margin: 0 0 6px; padding-bottom: 10px;
                     border-bottom: 2px solid #e94560; }
  .meth-section h3 { font-size: 14px; font-weight: 700; color: #1e293b;
                     margin: 20px 0 6px; }
  .meth-section p  { font-size: 13px; color: #334155; line-height: 1.7; margin: 0 0 10px; }
  .meth-section ul, .meth-section ol { font-size: 13px; color: #334155;
                     line-height: 1.7; margin: 0 0 10px; padding-left: 22px; }
  .formula { background: #f8fafc; border-left: 3px solid #e94560;
             padding: 10px 16px; margin: 10px 0 14px;
             font-family: 'Consolas', 'Courier New', monospace; font-size: 12.5px;
             color: #1e293b; white-space: pre; overflow-x: auto; }
  .meth-table { border-collapse: collapse; width: 100%; font-size: 13px;
                margin: 10px 0 16px; }
  .meth-table th { background: #f1f5f9; text-align: left; padding: 8px 14px;
                   font-weight: 600; color: #475569; font-size: 11px;
                   text-transform: uppercase; letter-spacing: .4px; }
  .meth-table td { padding: 7px 14px; border-top: 1px solid #f0f4f8;
                   color: #334155; vertical-align: top; }
  .meth-table tr:hover td { background: #f8fafc; }
  .meth-note { background: #fffbeb; border: 1px solid #fcd34d;
               border-radius: 6px; padding: 10px 14px; margin: 10px 0 14px;
               font-size: 12.5px; color: #78350f; line-height: 1.6; }
  .meth-card { background: #fff; border-radius: 10px;
               box-shadow: 0 1px 3px rgba(0,0,0,.06), 0 4px 16px rgba(0,0,0,.05);
               border: 1px solid #e8edf3; padding: 24px 28px; margin-bottom: 24px; }

  /* --- PDF export button --- */
  #pdf-export-btn { margin-left: 8px; padding: 9px 18px 10px;
                    border: none; border-radius: 6px; cursor: pointer;
                    font-size: 12px; font-weight: 600; font-family: inherit;
                    color: #fff; background: #e94560;
                    display: inline-flex; align-items: center; gap: 6px;
                    transition: background .12s, opacity .12s;
                    white-space: nowrap; flex-shrink: 0; align-self: center; }
  #pdf-export-btn:hover    { background: #c73652; }
  #pdf-export-btn:disabled { opacity: .55; cursor: wait; }
  @media print { #pdf-export-btn { display: none !important; } }

  /* --- HTML export button --- */
  #html-export-btn { margin-left: auto; padding: 9px 18px 10px;
                     border: none; border-radius: 6px; cursor: pointer;
                     font-size: 12px; font-weight: 600; font-family: inherit;
                     color: #fff; background: #0f766e;
                     display: inline-flex; align-items: center; gap: 6px;
                     transition: background .12s, opacity .12s;
                     white-space: nowrap; flex-shrink: 0; align-self: center; }
  #html-export-btn:hover    { background: #115e59; }
  #html-export-btn:disabled { opacity: .55; cursor: wait; }
  @media print { #html-export-btn { display: none !important; } }
</style>
<script>
function showDay(btn, panelId) {
  document.querySelectorAll('.day-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.day-panel').forEach(p => {
    p.classList.remove('active');
    p.style.display = 'none';
  });
  btn.classList.add('active');
  var panel = document.getElementById(panelId);
  panel.classList.add('active');
  panel.style.display = 'block';
  // Resize all Plotly charts that just became visible
  panel.querySelectorAll('.js-plotly-plot').forEach(function(el) {
    Plotly.Plots.resize(el);
  });
}
window.addEventListener('load', function() {
  // Ensure the first panel is shown and charts are sized correctly
  var first = document.querySelector('.day-panel.active');
  if (first) {
    first.style.display = 'block';
    first.querySelectorAll('.js-plotly-plot').forEach(function(el) {
      Plotly.Plots.resize(el);
    });
  }
});
function _clipboardCopy(btn, text) {
  var orig = btn.innerHTML;
  function flash() {
    btn.textContent = '\u2713 Copied!';
    setTimeout(function() { btn.innerHTML = orig; }, 1500);
  }
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(flash);
  } else {
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    flash();
  }
}
function copyChartJson(btn, id) {
  var el = document.getElementById(id);
  if (!el) return;
  _clipboardCopy(btn, el.textContent);
}
function copyRawData(btn, id) {
  var el = document.getElementById(id);
  if (!el) return;
  _clipboardCopy(btn, el.textContent);
}

// ── PDF Export ────────────────────────────────────────────────────────────────
async function exportPDF() {
  var btn = document.getElementById('pdf-export-btn');
  var origHTML = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Capturing\u2026'; }

  try {
    // 1. Collect panels in tab-bar order via data-panel attributes
    var tabBtns = Array.from(document.querySelectorAll('.day-btn[data-panel]'));

    // 2. Capture every Plotly chart as a PNG data URL.
    //    For charts with toggle menus (updatemenus), capture each variant.
    //    Temporarily show hidden panels so Plotly.toImage() has a layout.
    var imageMap = {};   // pd.id → [{url, label}, ...]
    for (var ti = 0; ti < tabBtns.length; ti++) {
      var panelEl = document.getElementById(tabBtns[ti].getAttribute('data-panel'));
      if (!panelEl) continue;
      var wasHidden = panelEl.style.display === 'none' || !panelEl.classList.contains('active');
      if (wasHidden) panelEl.style.display = 'block';
      var plotDivs = Array.from(panelEl.querySelectorAll('.js-plotly-plot'));
      for (var ci = 0; ci < plotDivs.length; ci++) {
        var pd = plotDivs[ci];
        try {
          var menus = (pd.layout && pd.layout.updatemenus) || [];
          var toggleMenus = [];
          for (var mi = 0; mi < menus.length; mi++) {
            if (menus[mi].buttons && menus[mi].buttons.length > 1) toggleMenus.push(menus[mi]);
          }
          if (toggleMenus.length > 0) {
            var origData = JSON.parse(JSON.stringify(pd.data));
            var origLayout = JSON.parse(JSON.stringify(pd.layout));
            var variants = [];
            for (var tmi = 0; tmi < toggleMenus.length; tmi++) {
              var tmenu = toggleMenus[tmi];
              for (var bi = 0; bi < tmenu.buttons.length; bi++) {
                var mbtn = tmenu.buttons[bi];
                var method = mbtn.method || 'restyle';
                await Plotly.react(pd, JSON.parse(JSON.stringify(origData)),
                                       JSON.parse(JSON.stringify(origLayout)));
                try {
                  if (method === 'update') {
                    await Plotly.update(pd, mbtn.args[0]||{}, mbtn.args[1]||{});
                  } else if (method === 'restyle') {
                    await Plotly.restyle(pd, mbtn.args[0]||{}, mbtn.args.length>1 ? mbtn.args[1] : undefined);
                  } else if (method === 'relayout') {
                    await Plotly.relayout(pd, mbtn.args[0]||{});
                  }
                } catch(ex) { console.warn('Toggle apply:', pd.id, ex); }
                Plotly.Plots.resize(pd);
                variants.push({
                  url: await Plotly.toImage(pd, {format:'png', width:900, height:520, scale:2}),
                  label: mbtn.label || ('View ' + (bi+1))
                });
              }
            }
            await Plotly.react(pd, origData, origLayout);
            imageMap[pd.id] = variants;
          } else {
            Plotly.Plots.resize(pd);
            imageMap[pd.id] = [{
              url: await Plotly.toImage(pd, {format:'png', width:900, height:520, scale:2}),
              label: null
            }];
          }
        } catch(e) { console.warn('exportPDF toImage failed:', pd.id, e); }
      }
      if (wasHidden) panelEl.style.display = 'none';
    }

    if (btn) btn.textContent = 'Building\u2026';

    // 3. Report-level metadata
    var reportTitle = (document.querySelector('header h1') || {}).textContent || 'ESS Analysis Report';
    var reportMeta  = (document.querySelector('header p')  || {}).textContent || '';
    var generated   = new Date().toLocaleString();

    // 4. Table of contents
    var tocItems = tabBtns.map(function(b) {
      var lbl = b.textContent.trim();
      var anchor = 'section-' + b.getAttribute('data-panel');
      return '<li><a href="#' + anchor + '">' + lbl + '</a></li>';
    }).join('');

    // 5. Build per-panel sections
    var sectionsHTML = '';
    for (var ti = 0; ti < tabBtns.length; ti++) {
      var panelId  = tabBtns[ti].getAttribute('data-panel');
      var tabLabel = tabBtns[ti].textContent.trim();
      var panelEl  = document.getElementById(panelId);
      var anchor   = 'section-' + panelId;

      sectionsHTML += '<div class="pdf-section" id="' + anchor + '"><h1>' + tabLabel + '</h1>';

      if (panelId === 'day-methodology') {
        var clone = panelEl.cloneNode(true);
        clone.querySelectorAll('.export-bar,.copy-btn,.copy-btn-raw,script').forEach(function(el){ el.remove(); });
        sectionsHTML += clone.innerHTML;
      } else if (panelEl) {
        var cards = Array.from(panelEl.querySelectorAll('.card'));
        if (!cards.length) sectionsHTML += '<p class="no-charts">No charts generated.</p>';
        cards.forEach(function(card) {
          var titleEl = card.querySelector('.card-meta h2');
          var chartTitle = titleEl ? titleEl.textContent.trim() : '';
          var methodText = '';
          card.querySelectorAll('.meta-item').forEach(function(item) {
            var lbl = item.querySelector('.lbl');
            if (lbl && lbl.textContent.trim().toLowerCase() === 'method') {
              var val = item.querySelector('.val');
              if (val) methodText = val.textContent.trim();
            }
          });
          var plotDiv = card.querySelector('.js-plotly-plot');
          var variants = (plotDiv && imageMap[plotDiv.id])
            ? imageMap[plotDiv.id]
            : [{url: null, label: null}];
          variants.forEach(function(v) {
            var displayTitle = chartTitle + (v.label ? ' \u2014 ' + v.label : '');
            var imgHTML = v.url
              ? '<img class="chart-img" src="' + v.url + '" alt="' + displayTitle.replace(/"/g,'&quot;') + '">'
              : (plotDiv ? '<p class="no-img">[Chart image could not be captured]</p>' : '');
            sectionsHTML += '<div class="pdf-card"><h2>' + displayTitle + '</h2>';
            sectionsHTML += '<p class="pdf-date">' + tabLabel + '</p>';
            sectionsHTML += imgHTML;
            if (methodText) sectionsHTML += '<p class="method-text"><strong>Method:</strong> ' + methodText + '</p>';
            sectionsHTML += '</div>';
          });
        });
      }
      sectionsHTML += '</div>';
    }

    // 6. Compose print document
    var css = [
      '*,*::before,*::after{box-sizing:border-box;}',
      'body{font-family:"Segoe UI",Arial,sans-serif;color:#1a1a2e;margin:0;padding:0;font-size:11pt;}',
      '.cover{page-break-after:always;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;min-height:100vh;padding:72px 48px;background:#1a1a2e;color:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact;}',
      '.cover h1{font-size:26pt;margin:0 0 14px;font-weight:800;}',
      '.cover p{font-size:11pt;color:#94a3b8;margin:4px 0;}',
      '.accent-line{width:50px;height:4px;background:#e94560;margin:28px 0;border-radius:2px;-webkit-print-color-adjust:exact;print-color-adjust:exact;}',
      '.toc-page{page-break-after:always;padding:36px 0;}',
      '.toc-page>h1{font-size:18pt;margin:0 0 24px;padding-bottom:10px;border-bottom:2px solid #e94560;}',
      '.toc-page ol{font-size:11pt;line-height:2.2;padding-left:22px;}',
      '.toc-page a{color:#1d4ed8;text-decoration:none;}',
      '.pdf-section{padding:28px 0 12px;}',
      '.pdf-section>h1{font-size:20pt;color:#0f172a;margin:0 0 32px;padding-bottom:12px;border-bottom:2px solid #e94560;page-break-after:avoid;}',
      '.pdf-card{margin-bottom:36px;page-break-inside:avoid;}',
      '.pdf-card>h2{font-size:13pt;color:#0f172a;margin:0 0 4px;page-break-after:avoid;}',
      '.pdf-date{font-size:10pt;color:#64748b;margin:0 0 10px;font-weight:500;}',
      '.chart-img{width:100%;max-width:100%;height:auto;border:1px solid #e8edf3;border-radius:6px;display:block;}',
      '.method-text{font-size:9.5pt;color:#475569;margin:8px 0 0;line-height:1.6;}',
      '.no-img,.no-charts{color:#94a3b8;font-style:italic;font-size:10pt;}',
      '.day-heading{display:none;}.container{padding:0;max-width:100%;}',
      '.meth-section h2{font-size:14pt;font-weight:700;color:#0f172a;margin:0 0 6px;padding-bottom:8px;border-bottom:2px solid #e94560;page-break-after:avoid;}',
      '.meth-section h3{font-size:11pt;font-weight:700;color:#1e293b;margin:16px 0 6px;page-break-after:avoid;}',
      '.meth-section p,.meth-section li{font-size:10pt;color:#334155;line-height:1.7;}',
      '.formula{background:#f8fafc;border-left:3px solid #e94560;padding:8px 14px;margin:8px 0 12px;font-family:"Consolas","Courier New",monospace;font-size:9pt;color:#1e293b;white-space:pre-wrap;page-break-inside:avoid;}',
      '.meth-table{border-collapse:collapse;width:100%;font-size:9.5pt;margin:8px 0 14px;page-break-inside:avoid;}',
      '.meth-table th{background:#f1f5f9;text-align:left;padding:6px 12px;font-weight:600;color:#475569;font-size:9pt;text-transform:uppercase;letter-spacing:.4px;}',
      '.meth-table td{padding:5px 12px;border-top:1px solid #f0f4f8;color:#334155;vertical-align:top;}',
      '.meth-note{background:#fffbeb;border:1px solid #fcd34d;border-radius:6px;padding:8px 12px;margin:8px 0 12px;font-size:9.5pt;color:#78350f;line-height:1.6;page-break-inside:avoid;}',
      '.meth-card{margin-bottom:20px;page-break-inside:avoid;}',
      '.export-bar,.copy-btn,.copy-btn-raw,.card-meta{display:none;}',
      '@page{size:A4 portrait;margin:20mm 24mm;}',
      '@media print{.pdf-section{page-break-before:always;}.toc-page{page-break-before:auto;}}'
    ].join('');

    var printHTML = '<!DOCTYPE html><html lang="en"><head>'
      + '<meta charset="UTF-8">'
      + '<title>' + reportTitle + ' - PDF</title>'
      + '<style>' + css + '</style>'
      + '</head><body>'
      + '<div class="cover"><h1>' + reportTitle + '</h1><div class="accent-line"></div>'
      + (reportMeta ? '<p>' + reportMeta + '</p>' : '')
      + '<p style="margin-top:16px;font-size:11pt">Generated ' + generated + '</p></div>'
      + '<div class="toc-page"><h1>Contents</h1><ol>' + tocItems + '</ol></div>'
      + sectionsHTML
      + '</body></html>';

    // 7. Open print window and trigger print dialog
    var win = window.open('', '_blank', 'width=1200,height=900,scrollbars=yes,resizable=yes');
    if (!win) {
      alert('Pop-up blocked — please allow pop-ups for this page and try again.');
      return;
    }
    win.document.open();
    win.document.write(printHTML);
    win.document.close();
    // 400 ms lets Chrome/Edge finish painting data-URL images before the print dialog opens
    win.setTimeout(function() { win.focus(); win.print(); }, 400);

  } catch(e) {
    console.error('exportPDF error:', e);
    alert('PDF export failed — see browser console for details.');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = origHTML; }
  }
}

// ── HTML Export ───────────────────────────────────────────────────────────────
function exportHTML() {
  var btn = document.getElementById('html-export-btn');
  var origHTML = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Preparing\u2026'; }

  try {
    // Clone the live DOM — Plotly scripts re-execute on load so charts stay interactive
    var clone = document.documentElement.cloneNode(true);

    // Remove xlsx download links (file:/// URIs that break outside original location)
    clone.querySelectorAll('.export-link').forEach(function(el) { el.remove(); });

    var titleEl = document.querySelector('header h1');
    var base = titleEl
      ? titleEl.textContent.trim().replace(/[^a-z0-9]+/gi, '_').replace(/_+$/, '')
      : 'asrs_report';
    var fname = base + '_standalone.html';

    var html = '<!DOCTYPE html>\\n' + clone.outerHTML;
    var blob = new Blob([html], {type: 'text/html;charset=utf-8'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = fname;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(function() { URL.revokeObjectURL(url); }, 5000);
  } catch(e) {
    console.error('exportHTML error:', e);
    alert('HTML export failed \u2014 see browser console for details.');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = origHTML; }
  }
}
</script>
</head>
<body>
<header>
  <h1>Hai Robotics ESS Log Analyzer</h1>
</header>
"""

_FOOT = """
<footer>ASRS Performance Analysis &mdash; generated by app.py</footer>
</body></html>
"""


# ── helpers ───────────────────────────────────────────────────────────────────

_CLIPBOARD_ICON = (
    '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<rect x="5" y="2" width="8" height="11" rx="1" stroke="currentColor" stroke-width="1.5"/>'
    '<path d="M5 4H4a1 1 0 00-1 1v8a1 1 0 001 1h7a1 1 0 001-1v-1" '
    'stroke="currentColor" stroke-width="1.5"/>'
    '<path d="M8 1h3v2H8V1z" stroke="currentColor" stroke-width="1.5" '
    'stroke-linejoin="round"/>'
    '</svg>'
)

_DOWNLOAD_ICON = (
    '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<path d="M8 1v9m0 0L5 7m3 3 3-3M2 12v1a1 1 0 001 1h10a1 1 0 001-1v-1" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round"/>'
    '</svg>'
)


def _chart_json_payload(entry: dict) -> str:
    """Return a JSON string of all chart trace data plus metadata."""
    fig: go.Figure = entry["figure"]
    fig_dict = json.loads(fig.to_json())
    payload = {
        "chart_id": entry.get("id", ""),
        "title":    entry.get("title", ""),
        "source":   entry.get("source", ""),
        "method":   entry.get("method", ""),
        "data":     fig_dict["data"],
    }
    return json.dumps(payload, ensure_ascii=False)


def _raw_data_payload(entry: dict) -> str:
    """
    Return a JSON string of the raw source data attached to this chart.
    Each analysis module stores the pre-aggregation tabular data in the
    optional ``raw_data`` key of the ChartResult dict.  The payload is
    fully serialised with no truncation.
    """
    raw = entry.get("raw_data")
    payload = {
        "chart_id": entry.get("id", ""),
        "title":    entry.get("title", ""),
        "source":   entry.get("source", ""),
        "method":   entry.get("method", ""),
        "data":     _json_safe(raw) if raw is not None else {},
    }
    return json.dumps(payload, ensure_ascii=False)


def _export_bar(entry: dict, outdir: str, json_id: str) -> str:
    """
    Return HTML for the export bar (xlsx download links + Copy JSON button +
    Copy Raw Data button) plus inline <script> tags for both payloads.

    Always rendered — even when there are no xlsx links.
    """
    raw_id = json_id + "-raw"

    hint = entry.get("export_hint", "")
    links_html = ""
    if hint:
        for fname in hint.split(" / "):
            fname = fname.strip()
            if not fname:
                continue
            fpath = os.path.join(outdir, fname).replace("\\", "/")
            uri   = "file:///" + urllib.parse.quote(fpath, safe=":/")
            links_html += (
                f'<a href="{uri}" class="export-link">'
                f'{_DOWNLOAD_ICON}{fname}</a>'
            )

    copy_btn = (
        f'<button class="copy-btn" onclick="copyChartJson(this,\'{json_id}\')">'
        f'{_CLIPBOARD_ICON}Copy JSON</button>'
    )
    copy_raw_btn = (
        f'<button class="copy-btn-raw" onclick="copyRawData(this,\'{raw_id}\')">'
        f'{_CLIPBOARD_ICON}Copy Raw Data</button>'
    )
    json_script = (
        f'<script id="{json_id}" type="application/json">'
        f'{_chart_json_payload(entry)}'
        f'</script>\n'
    )
    raw_script = (
        f'<script id="{raw_id}" type="application/json">'
        f'{_raw_data_payload(entry)}'
        f'</script>\n'
    )
    bar = (
        f'  <div class="export-bar">'
        f'<span class="export-label">Export</span>'
        f'{links_html}{copy_btn}{copy_raw_btn}</div>\n'
    )
    return json_script + raw_script + bar


# ── methodology panel ─────────────────────────────────────────────────────────

def _methodology_html() -> str:
    return """
<div class="container">
<h2 class="day-heading">Analysis Methodology</h2>

<!-- ── Data Sources ─────────────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>Data Sources</h2>
<p>The analyser auto-detects three sheets inside each Excel workbook by their column signatures.</p>
<table class="meth-table">
<tr><th>Internal key</th><th>Typical sheet name</th><th>Detected by</th></tr>
<tr><td><code>callback</code></td><td>回调明细</td><td>Columns <code>动作类型</code> + <code>位置类型</code></td></tr>
<tr><td><code>station</code></td><td>labor_station_record</td><td>Column <code>事件类型</code> (without lifecycle columns)</td></tr>
<tr><td><code>lifecycle</code></td><td>任务生命周期</td><td>Column <code>任务全程耗时(秒)</code> or <code>K50完成耗时(秒)</code></td></tr>
</table>

<h3>Key event definitions — labor_station_record</h3>
<table class="meth-table">
<tr><th>Event (<code>事件类型</code>)</th><th>Meaning</th></tr>
<tr><td><code>arrived</code></td><td>Robot docks at the LABOR station; operator begins picking</td></tr>
<tr><td><code>triggerGo</code></td><td>Operator finishes pick and releases the robot</td></tr>
<tr><td><code>release</code></td><td>Robot physically leaves the docking bay</td></tr>
<tr><td><code>ppReady</code></td><td>Station signals readiness for the next robot</td></tr>
</table>

<div class="meth-note">
<strong>Important:</strong> The callback sheet's <code>complete</code> event fires at robot <em>arrival</em>
(same timestamp as <code>arrived</code> in the station record), <strong>not</strong> at pick completion.
All pick-completion counts and pick-time measurements therefore use <code>triggerGo</code> from the station record.
</div>
</div>

<!-- ── 1. Throughput ─────────────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>1 · Throughput per Station per Hour</h2>
<p><strong>Source:</strong> labor_station_record &nbsp;|&nbsp; <strong>Robot filter:</strong> K50 only</p>

<h3>1.1 Throughput count</h3>
<p>A task is counted as complete when a <code>triggerGo</code> event is logged for a K50 robot at a LABOR station.</p>
<div class="formula">T(station, hour) = count of triggerGo events
                   where floor(timestamp, 1 h) == hour
                   AND   robot_type == K50</div>
<p>The hour is bucketed by the <code>triggerGo</code> timestamp.</p>

<h3>1.2 Implied throughput ceiling</h3>
<p>The theoretical maximum throughput a station can sustain given observed pick times:</p>
<div class="formula">ImpliedTPH(station, hour) = 3 600 / (AvgPickTime(station, hour) + 6)</div>
<p><code>AvgPickTime</code> is the mean <code>arrived→triggerGo</code> duration (s) for that station-hour, also bucketed by <code>triggerGo</code> time. The constant <strong>6 s</strong> is a fixed robot-handoff overhead added to every cycle.</p>

<h3>1.3 Utilisation %</h3>
<div class="formula">Utilisation(station, hour) = T(station, hour) / ImpliedTPH(station, hour) × 100 %</div>
<ul>
<li><strong>~100 %</strong> — robot-supply-limited (robots arrive as fast as picks allow)</li>
<li><strong>&lt; 100 %</strong> — pick capacity exists but robots are not arriving fast enough (upstream gap)</li>
</ul>

<h3>1.4 Design-rate utilisation</h3>
<div class="formula">DesignUtil(station, hour) = T(station, hour) / DesignRate(station) × 100 %</div>
<p>Only shown when design rates are configured in <code>asrs_config.json</code>.</p>

<h3>Charts</h3>
<table class="meth-table">
<tr><th>Chart</th><th>Description</th></tr>
<tr><td>Total Throughput by Hour</td><td>Sum of triggerGo events across all stations per hour. Peak hour highlighted.</td></tr>
<tr><td>Throughput per Station per Hour</td><td>Station × hour heatmap. Toggle between actual count, Actual/Target, % of design rate, and % of implied throughput.</td></tr>
<tr><td>Station Utilisation vs Design Rate</td><td>Station × hour heatmap of % of configured design rate.</td></tr>
</table>
</div>

<!-- ── 2. Pick Time ──────────────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>2 · Operator Pick Time (Dwell Time)</h2>
<p><strong>Source:</strong> labor_station_record &nbsp;|&nbsp; <strong>Robot filter:</strong> K50 only</p>

<h3>2.1 Pick time measurement</h3>
<div class="formula">PickTime = triggerGo.timestamp − arrived.timestamp   (seconds)</div>
<p>Valid range: 0 &lt; PickTime &lt; 3 600 s. Events are paired per robot in chronological order. If two <code>arrived</code> events occur without an intervening <code>triggerGo</code>, the later <code>arrived</code> overwrites the earlier one.</p>
<p>Bucketing uses the <code>arrived</code> timestamp so the distribution reflects when work started.</p>

<h3>2.2 Implied throughput</h3>
<div class="formula">ImpliedTPH(station, hour) = 3 600 / (MeanPickTime(station, hour) + 6)</div>

<h3>2.3 Distribution</h3>
<ul>
<li>Bin width: 2 s</li>
<li>Smoothing: Gaussian kernel, σ = 2.5 bins</li>
<li>X-axis cap: min(99th percentile, 180 s)</li>
<li>Outlier removal: Tukey IQR fencing (1.5 × IQR) applied independently to pick times and implied throughput</li>
</ul>

<h3>2.4 OLS regression</h3>
<div class="formula">Y = a + b · X
  X = MeanPickTime(station, hour)   (seconds)
  Y = actual triggerGo count        (tasks / hr)</div>
<p>R² measures the fraction of throughput variance explained by pick time alone.</p>

<h3>Charts</h3>
<table class="meth-table">
<tr><th>Chart</th><th>Description</th></tr>
<tr><td>Operator Pick Time at Workstations</td><td>Median / average pick time per station per hour. Toggle to Implied Throughput.</td></tr>
<tr><td>Pick Time Distribution by Workstation</td><td>Gaussian-smoothed histogram per station.</td></tr>
<tr><td>Pick Time vs Actual Throughput — OLS Fit</td><td>Scatter + OLS regression line per station.</td></tr>
</table>
</div>

<!-- ── 3. Switch Time ────────────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>3 · Robot Switch Time</h2>
<p><strong>Source:</strong> labor_station_record</p>

<h3>3.1 Definition</h3>
<p>Switch time is the gap between one robot leaving a station and the next robot arriving:</p>
<div class="formula">SwitchTime = next_arrived.timestamp − release.timestamp   (seconds)</div>
<p>Measured per station sequentially: for each <code>release</code> event, the switch time is the gap to the immediately following <code>arrived</code> at the same station, regardless of robot identity. Valid range: 0–7 200 s.</p>

<h3>3.2 Aggregation</h3>
<div class="formula">MedianSwitchTime(station, hour) = median { SwitchTime_i : station, hour }
MeanSwitchTime(station, hour)   = mean   { SwitchTime_i : station, hour }</div>
<p>Colour scale anchored to the 95th percentile to prevent outliers from compressing the useful range.</p>

<h3>Charts</h3>
<table class="meth-table">
<tr><th>Chart</th><th>Description</th></tr>
<tr><td>Robot Switch Time at Workstations</td><td>Median (default) / average switch time per station per hour.</td></tr>
</table>
</div>

<!-- ── 4. Cycle Time ─────────────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>4 · Cycle Time <span style="color:#94a3b8; font-size:12px; font-weight:normal;">(temporarily disabled)</span></h2>
<p><strong>Source:</strong> lifecycle (primary), callback (demand correlation)</p>
<div class="meth-note">
<strong>Note:</strong> Cycle time analysis is temporarily disabled in the current build.
The methodology is documented here for reference. When re-enabled, these charts will
appear automatically for files that contain a lifecycle sheet.
</div>

<h3>4.1 Total cycle time</h3>
<div class="formula">CycleTime = 任务全程耗时(秒) / 60   (minutes)</div>
<p>Valid range: 0–7 200 s raw. Histogram capped at 30 min. Only tasks whose destination (<code>目标位置</code>) starts with <code>LABOR</code> are included. Percentiles computed: <strong>median</strong>, <strong>p90</strong>, <strong>p99</strong>.</p>

<h3>4.2 Stage durations</h3>
<p>Columns whose name contains <code>耗时(秒)</code> (excluding the total) are treated as stage columns. Stage medians are computed <strong>independently</strong> — not summed — so that tasks with one missing stage do not distort the others.</p>
<table class="meth-table">
<tr><th>Column pattern</th><th>Stage</th></tr>
<tr><td><code>分配耗时(秒)</code></td><td>Allocation wait</td></tr>
<tr><td><code>A42取箱耗时(秒)</code></td><td>A42 retrieve (shuttle picks from shelf)</td></tr>
<tr><td><code>A42放箱耗时(秒)</code></td><td>A42 deposit (shuttle places at handoff point)</td></tr>
<tr><td><code>K50完成耗时(秒)</code></td><td>K50 deliver + dwell (AMR delivers to LABOR and waits for pick)</td></tr>
<tr><td><code>拣选耗时(秒)</code></td><td>Picking (operator pick time)</td></tr>
</table>

<h3>4.3 Demand vs cycle time correlation</h3>
<div class="formula">Pearson r  = Σ[(D_i − D̄)(C_i − C̄)] / (n · σ_D · σ_C)
Spearman ρ = Pearson r of the rank-transformed series

  D_i = hourly demand (callback complete events at LABOR stations)
  C_i = median cycle time in that hour</div>

<h3>Charts (when enabled)</h3>
<table class="meth-table">
<tr><th>Chart</th><th>Description</th></tr>
<tr><td>Cycle Time Distribution</td><td>Histogram (30-min cap, 2-min bins). Median, p90, p99 annotated.</td></tr>
<tr><td>Median Cycle Time by Hour</td><td>Line chart of median cycle time per clock hour.</td></tr>
<tr><td>Throughput Demand vs Cycle Time</td><td>Dual-axis: demand bars + cycle time line, plus scatter with Pearson r and Spearman ρ.</td></tr>
<tr><td>Median Cycle Time Stage Composition</td><td>Donut of median stage durations.</td></tr>
<tr><td>Cycle Time Stage Composition by Workstation</td><td>Stacked bar of stage medians per destination station.</td></tr>
</table>
</div>

<!-- ── 5. Retrieval ──────────────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>5 · Retrieval Demand</h2>
<p><strong>Source:</strong> lifecycle</p>

<h3>5.1 Source location parsing</h3>
<p>Each task's origin is in <code>起始位置</code> with the format <code>HAI-&lt;aisle&gt;-&lt;bay&gt;-&lt;level&gt;-&lt;col&gt;</code>. Non-storage origins are excluded.</p>
<div class="formula">Aisle = part[1]
Bay   = part[2]
Level = part[3]</div>

<h3>5.2 Hot-aisle threshold</h3>
<div class="formula">Hot aisle: retrievals(aisle) &gt; 1.5 × mean(retrievals across all aisles)</div>
<p>Hot aisles are highlighted in the accent colour on the bar chart.</p>

<h3>5.3 Bay heatmap colour scale</h3>
<p>The colour maximum is anchored to the <strong>97th percentile</strong> of bay retrieval counts, preventing a single busy bay from washing out the rest of the grid.</p>

<h3>5.4 Tote Pareto</h3>
<p>Totes are ranked by retrieval frequency (most → least). The Pareto curve shows:</p>
<div class="formula">X = cumulative % of unique tote IDs (ranked most → least frequent)
Y = cumulative % of total retrievals</div>
<p>Reference lines mark the <strong>5 %</strong> and <strong>20 %</strong> tote thresholds.</p>

<h3>Charts</h3>
<table class="meth-table">
<tr><th>Chart</th><th>Description</th></tr>
<tr><td>Retrieval Demand by Storage Aisle</td><td>Bar chart; hot aisles highlighted.</td></tr>
<tr><td>Retrieval Demand Heatmap</td><td>Aisle × bay heatmap (97th-percentile colour scale).</td></tr>
<tr><td>Tote-Level Retrieval Concentration</td><td>Cumulative retrieval Pareto curve.</td></tr>
</table>
</div>

<!-- ── 6. Fleet Utilisation ──────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>6 · Fleet Utilisation</h2>
<p><strong>Source:</strong> lifecycle (primary), station record (fallback)</p>

<h3>6.1 Delivery interval</h3>
<div class="formula">interval_start = complete_ts − K50完成耗时(秒)
interval_end   = complete_ts</div>

<h3>6.2 Concurrent robot count (queue depth)</h3>
<div class="formula">concurrent(i) = count of tasks j ≠ i at same station where:
    interval_start[j] ≤ interval_end[i]
    AND
    interval_end[j]   ≥ interval_start[i]</div>
<p>Vectorised O(n²) comparison for n ≤ 5 000 rows; row-by-row loop for larger datasets.</p>
<div class="formula">Pearson r = linear correlation(concurrent, leg_duration)</div>
<p><strong>Fallback</strong> (lifecycle absent): intervals constructed from <code>arrived → release</code> pairs in the station record.</p>

<h3>6.3 Real-time fleet utilisation</h3>
<p>Bin width: 5 minutes.</p>
<div class="formula">K50_concurrent(t) = count of tasks where interval_start ≤ t ≤ interval_end
K50_util%(t)      = K50_concurrent(t) / total_K50_fleet × 100 %</div>
<p>A42 (shuttle) utilisation is derived from stage columns or from <code>arrived → release</code> station pairs as fallback.</p>

<h3>Charts</h3>
<table class="meth-table">
<tr><th>Chart</th><th>Description</th></tr>
<tr><td>Robots assigned per station vs delivery time</td><td>Box plots of queue depth + median leg duration vs queue depth. Pearson r shown.</td></tr>
<tr><td>Real-Time Fleet Utilisation Profile</td><td>5-minute time series of K50 and A42 fleet utilisation %.</td></tr>
</table>
</div>

<!-- ── 7. Summary ────────────────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>7 · Summary — Cross-Day Trends</h2>
<p><em>Generated only when ≥ 2 days are loaded.</em></p>
<p><strong>Source:</strong> station record + callback sheets across all loaded days; lifecycle sheet where available.</p>

<h3>7.1 Per-day metrics</h3>
<table class="meth-table">
<tr><th>Metric</th><th>Formula</th></tr>
<tr><td>Total tasks</td><td>Count of <code>triggerGo</code> events at LABOR stations (station record)</td></tr>
<tr><td>Median cycle time (min)</td><td>median(<code>任务全程耗时(秒)</code>) / 60, filtered 0–7 200 s</td></tr>
<tr><td>p90 cycle time (min)</td><td>90th percentile of the same series</td></tr>
<tr><td>Avg pick time (s)</td><td>Mean of all <code>arrived→triggerGo</code> durations, 0–3 600 s</td></tr>
<tr><td>Median switch time (s)</td><td>Median of all <code>release→arrived</code> gaps, 0–7 200 s</td></tr>
</table>

<h3>7.2 Pick rate utilisation %</h3>
<p>For each station-hour, the fraction of throughput captured relative to the operator-speed ceiling:</p>
<div class="formula">ImpliedTPH(station, hour) = 3 600 / (AvgPickTime(station, hour) + 6)
Util%(station, hour)     = ActualCompletions / ImpliedTPH × 100</div>
<p>The per-station per-day value is the mean of Util% across all active hours for that station.
100 % means the station was producing exactly as fast as operator speed allows;
values below 100 % indicate robot-supply gaps or non-pick losses.</p>

<h3>Charts</h3>
<table class="meth-table">
<tr><th>Chart</th><th>Description</th></tr>
<tr><td>Day-over-Day Performance Trends</td><td>Total completed tasks (bar) and median cycle time (line) per day, side by side.</td></tr>
<tr><td>Average Operator Pick Time Trend</td><td>Per-station and overall pick-time trend lines. Toggle between per-station and combined views.</td></tr>
<tr><td>Average % of Implied Pick Rate per Station per Day</td><td>Per-station and overall utilisation % trend. Shows how much of the operator-speed ceiling each station achieves per day.</td></tr>
</table>
</div>

<!-- ── Constants ─────────────────────────────────────────────────────────── -->
<div class="meth-card meth-section">
<h2>Constants Reference</h2>
<table class="meth-table">
<tr><th>Constant</th><th>Value</th><th>Used in</th></tr>
<tr><td>Switch overhead</td><td>6 s</td><td>§1.2 Implied throughput, §2.2 Implied throughput, §7.2 Pick rate utilisation</td></tr>
<tr><td>Pick time valid range</td><td>0–3 600 s</td><td>§2.1, §7.1</td></tr>
<tr><td>Switch time valid range</td><td>0–7 200 s</td><td>§3.1, §7.1</td></tr>
<tr><td>Cycle time valid range</td><td>0–7 200 s</td><td>§4.1 (disabled)</td></tr>
<tr><td>Histogram cap</td><td>30 min</td><td>§4.1 (disabled)</td></tr>
<tr><td>Hot-aisle multiplier</td><td>1.5 × mean</td><td>§5.2</td></tr>
<tr><td>Bay heatmap colour cap</td><td>97th percentile</td><td>§5.3</td></tr>
<tr><td>Switch-time colour cap</td><td>95th percentile</td><td>§3.2</td></tr>
<tr><td>IQR fence multiplier</td><td>1.5 ×</td><td>§2.4 outlier removal</td></tr>
<tr><td>Pick distribution σ</td><td>2.5 bins (2 s each)</td><td>§2.3 Gaussian smoothing</td></tr>
<tr><td>Fleet utilisation bin</td><td>5 min</td><td>§6.3</td></tr>
<tr><td>Overlap vectorisation threshold</td><td>n ≤ 5 000</td><td>§6.2</td></tr>
</table>
</div>

</div>
"""


# ── public function ───────────────────────────────────────────────────────────

def generate_html_report(
    outdir: str,
    days: list[dict],
    summary_registry: list[dict] | None = None,
) -> str:
    """
    Build a self-contained HTML report.

    Parameters
    ----------
    outdir : str
        Directory where ``asrs_analysis_report.html`` is written.
    days : list[dict]
        Each dict: ``{"label": str, "registry": [chart_dict, ...]}``.
        chart_dict keys: ``id``, ``title``, ``figure`` (go.Figure),
        ``source``, ``method``, ``export_hint``.
    summary_registry : list[dict] | None
        Optional list of cross-day trend chart dicts.  When provided and
        non-empty, a "Summary" tab is prepended to the tab bar.

    Returns
    -------
    str
        Absolute path to the written HTML file.
    """
    multi          = len(days) > 1
    has_summary    = multi and bool(summary_registry)
    chunks = [_HEAD]

    # ── tab bar (always rendered — Methodology tab is always present) ───────────
    chunks.append('<div class="day-bar">\n')
    # Summary tab comes first (multi-day only)
    if has_summary:
        chunks.append(
            '  <button class="day-btn active" '
            'data-panel="day-summary" '
            'onclick="showDay(this,\'day-summary\')">Summary</button>\n'
        )
    for i, day in enumerate(days):
        active = " active" if (i == 0 and not has_summary) else ""
        chunks.append(
            f'  <button class="day-btn{active}" '
            f'data-panel="day-{i}" '
            f'onclick="showDay(this,\'day-{i}\')">'
            f'{day["label"]}</button>\n'
        )
    # Methodology tab always last
    chunks.append(
        '  <button class="day-btn" '
        'data-panel="day-methodology" '
        'onclick="showDay(this,\'day-methodology\')">Methodology</button>\n'
    )
    # Export buttons — pushed to the far right via margin-left:auto on the first one
    _HTML_ICON = (
        '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" '
        'xmlns="http://www.w3.org/2000/svg">'
        '<path d="M2 4l4 4-4 4M8 12h6" '
        'stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )
    _PDF_ICON = (
        '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" '
        'xmlns="http://www.w3.org/2000/svg">'
        '<path d="M3 12v1a1 1 0 001 1h8a1 1 0 001-1v-1'
        'M8 2v8m0 0L5.5 7.5M8 10l2.5-2.5" '
        'stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )
    chunks.append(
        f'  <button id="html-export-btn" onclick="exportHTML()">'
        f'{_HTML_ICON}Export HTML</button>\n'
    )
    chunks.append(
        f'  <button id="pdf-export-btn" onclick="exportPDF()">'
        f'{_PDF_ICON}Export PDF</button>\n'
    )
    chunks.append('</div>\n')

    # ── summary panel ─────────────────────────────────────────────────────────
    if has_summary:
        chunks.append(
            '<div id="day-summary" class="day-panel active" style="display:block">\n'
        )
        chunks.append('<div class="container">\n')
        chunks.append('<h2 class="day-heading">Summary — Trends Across Days</h2>\n')
        for chart_idx, entry in enumerate(summary_registry):
            chart_id   = f'dsummaryc{chart_idx}'
            fig: go.Figure = entry["figure"]
            include_js = (chart_idx == 0)
            chart_html = fig.to_html(
                full_html=False, include_plotlyjs=include_js,
                div_id=chart_id, default_width="100%", default_height="480px",
                config={
                    "displayModeBar": True, "displaylogo": False, "responsive": True,
                    "toImageButtonOptions": {"format": "png", "scale": 3},
                },
            )
            chunks.append('<div class="card">\n')
            chunks.append(f'  <div class="card-meta"><h2>{entry["title"]}</h2>\n')
            meta_items = ""
            if entry.get("source"):
                meta_items += (f'<span class="meta-item">'
                               f'<span class="lbl">Source</span>'
                               f'<span class="val">{entry["source"]}</span></span>')
            if entry.get("method"):
                meta_items += (f'<span class="meta-item">'
                               f'<span class="lbl">Method</span>'
                               f'<span class="val">{entry["method"]}</span></span>')
            if meta_items:
                chunks.append(f'  <div class="meta-row">{meta_items}</div>\n')
            chunks.append('  </div>\n')
            chunks.append(f'  <div class="chart-wrap">{chart_html}</div>\n')
            chunks.append(_export_bar(entry, outdir, f"jd-summary-{chart_idx}"))
            chunks.append('</div>\n')
        chunks.append('</div>\n')   # .container
        chunks.append('</div>\n')   # .day-panel

    # ── day panels ────────────────────────────────────────────────────────────
    # First day panel is active when there is no summary tab
    for day_idx, day in enumerate(days):
        active  = " active" if (day_idx == 0 and not has_summary) else ""
        display = "block"   if (day_idx == 0 and not has_summary) else "none"
        panel_id = f"day-{day_idx}"

        chunks.append(
            f'<div id="{panel_id}" class="day-panel{active}" '
            f'style="display:{display}">\n'
        )
        chunks.append('<div class="container">\n')

        # Always show the day label since there is always a tab bar
        chunks.append(f'<h2 class="day-heading">{day["label"]}</h2>\n')

        registry: list[dict] = day.get("registry", [])
        if not registry:
            chunks.append('<p style="color:#94a3b8;font-style:italic">No charts generated.</p>\n')
        else:
            for chart_idx, entry in enumerate(registry):
                chart_id = f'd{day_idx}c{chart_idx}'
                fig: go.Figure = entry["figure"]

                # Embed plotly div — include plotlyjs only on the very first chart
                # overall; if a summary panel was already emitted it already included it.
                include_js = (day_idx == 0 and chart_idx == 0 and not has_summary)
                chart_html = fig.to_html(
                    full_html=False,
                    include_plotlyjs=include_js,
                    div_id=chart_id,
                    default_width="100%",
                    default_height="480px",
                    config={"displayModeBar": True, "displaylogo": False,
                            "responsive": True,
                            "toImageButtonOptions": {"format": "png", "scale": 3}},
                )

                source_val = entry.get("source", "")
                method_val = entry.get("method", "")
                meta_items = ""
                if source_val:
                    meta_items += (f'<span class="meta-item">'
                                   f'<span class="lbl">Source</span>'
                                   f'<span class="val">{source_val}</span></span>')
                if method_val:
                    meta_items += (f'<span class="meta-item">'
                                   f'<span class="lbl">Method</span>'
                                   f'<span class="val">{method_val}</span></span>')

                chunks.append('<div class="card">\n')
                chunks.append(f'  <div class="card-meta"><h2>{entry["title"]}</h2>\n')
                if meta_items:
                    chunks.append(f'  <div class="meta-row">{meta_items}</div>\n')
                chunks.append('  </div>\n')
                chunks.append(f'  <div class="chart-wrap">{chart_html}</div>\n')

                day_outdir = day.get("outdir", outdir)
                chunks.append(_export_bar(entry, day_outdir, f"jd-d{day_idx}c{chart_idx}"))

                chunks.append('</div>\n')   # .card

        chunks.append('</div>\n')   # .container
        chunks.append('</div>\n')   # .day-panel

    # ── methodology panel (always present, never the default active tab) ──────
    chunks.append('<div id="day-methodology" class="day-panel" style="display:none">\n')
    chunks.append(_methodology_html())
    chunks.append('</div>\n')

    chunks.append(_FOOT)

    out_path = os.path.join(outdir, "asrs_analysis_report.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("".join(chunks))

    return out_path
