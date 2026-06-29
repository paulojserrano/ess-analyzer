#!/usr/bin/env python3
"""
ai-codex for ESS Analyzer (Python + Tkinter + Plotly)
Generates a compact codebase index for AI context injection.
"""

import os
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Path Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = SCRIPT_DIR
TODAY = datetime.today().strftime('%Y-%m-%d')
OUTPUT_DIR = os.path.join(ROOT, '.ai-codex')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_file_safe(filepath: str) -> str:
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception:
        return ''

def pad(string: str, length: int) -> str:
    return string.ljust(length)

def extract_module_docstring(content: str) -> str:
    """Extract the first triple-quoted string at the top of a module."""
    match = re.match(r'\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', content, re.DOTALL)
    if match:
        doc = (match.group(1) or match.group(2)).strip()
        # Collapse to first line only
        return doc.split('\n')[0].strip()
    return ''

def extract_classes(content: str) -> list[dict]:
    """Extract class names and their method signatures."""
    classes = []
    # Find class definitions
    class_pattern = re.compile(r'^class\s+(\w+)(?:\(([^)]*)\))?:', re.MULTILINE)
    for m in class_pattern.finditer(content):
        name = m.group(1)
        base = m.group(2) or ''
        # Find methods within this class (until next class or EOF)
        class_start = m.end()
        next_class = class_pattern.search(content, class_start)
        class_body = content[class_start: next_class.start() if next_class else len(content)]
        methods = extract_functions(class_body, method=True)
        classes.append({'name': name, 'base': base, 'methods': methods})
    return classes

def extract_functions(content: str, method: bool = False) -> list[dict]:
    """Extract function/method names and their parameter signatures."""
    results = []
    # Match def lines, handling async def and indentation
    if method:
        # Methods: indented defs
        pattern = re.compile(r'^\s{4,}(?:async\s+)?def\s+(\w+)\s*\((.*?)\)', re.MULTILINE)
    else:
        # Module-level defs: no or minimal indentation
        pattern = re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\((.*?)\)', re.MULTILINE)

    for m in pattern.finditer(content):
        name = m.group(1)
        params = m.group(2).strip()
        if len(params) > 60:
            params = params[:57] + '...'
        results.append({'name': name, 'params': params})
    return results

def extract_chart_ids(content: str) -> list[dict]:
    """
    Extract chart id and title pairs from analysis modules.
    Looks for patterns like:
        "id": "some_id",
        "title": "Some Title",
    """
    results = []
    # Find all dict-literal pairs near each other
    id_pattern = re.compile(r'"id"\s*:\s*"([^"]+)"')
    title_pattern = re.compile(r'"title"\s*:\s*"([^"]+)"')

    id_matches = [(m.start(), m.group(1)) for m in id_pattern.finditer(content)]
    title_matches = [(m.start(), m.group(1)) for m in title_pattern.finditer(content)]

    # Pair each id with the nearest title within 300 chars
    used_titles = set()
    for id_pos, id_val in id_matches:
        best_title = None
        best_dist = 9999
        for t_pos, t_val in title_matches:
            dist = abs(t_pos - id_pos)
            if dist < best_dist and dist < 300 and t_val not in used_titles:
                best_dist = dist
                best_title = t_val
        if best_title:
            used_titles.add(best_title)
            results.append({'id': id_val, 'title': best_title})

    return results

def extract_constants(content: str) -> list[dict]:
    """Extract top-level constant assignments (UPPER_CASE names)."""
    results = []
    pattern = re.compile(r'^([A-Z][A-Z0-9_]+)\s*=\s*(.+)', re.MULTILINE)
    for m in pattern.finditer(content):
        name = m.group(1)
        value = m.group(2).strip()
        if len(value) > 60:
            value = value[:57] + '...'
        results.append({'name': name, 'value': value})
    return results

# ---------------------------------------------------------------------------
# 1. analyses-index.md
# ---------------------------------------------------------------------------

def generate_analyses_index() -> str:
    analyses_dir = os.path.join(ROOT, 'analyses')
    if not os.path.exists(analyses_dir):
        return None

    py_files = sorted(
        f for f in os.listdir(analyses_dir)
        if f.endswith('.py') and f != '__init__.py'
    )
    if not py_files:
        return None

    output = [
        f"# Analyses Index (generated {TODAY})",
        f"# Each module exports run(data, cfg) -> list[ChartResult]",
        f"# ChartResult keys: id, title, figure, source, method, export_hint",
        ""
    ]

    for filename in py_files:
        filepath = os.path.join(analyses_dir, filename)
        content = read_file_safe(filepath)
        if not content:
            continue

        module_name = filename[:-3]
        docstring = extract_module_docstring(content)
        line_count = len(content.splitlines())
        charts = extract_chart_ids(content)
        fns = extract_functions(content)

        output.append(f"## {module_name}.py  ({line_count} lines)")
        if docstring:
            output.append(f"   {docstring}")
        output.append("")

        if charts:
            output.append("   Charts:")
            for c in charts:
                output.append(f"     {pad(c['id'], 35)} {c['title']}")
        else:
            output.append("   Charts: (no chart IDs detected)")

        if fns:
            output.append("")
            output.append("   Functions:")
            for fn in fns:
                output.append(f"     {pad('def ' + fn['name'], 30)} ({fn['params']})")
        output.append("")

    # Also document __init__.py ChartResult schema
    init_path = os.path.join(analyses_dir, '__init__.py')
    init_content = read_file_safe(init_path)
    if init_content:
        output.append("## __init__.py — ChartResult schema")
        output.append("   Keys expected in every dict returned by run():")
        for key in ['id', 'title', 'figure', 'source', 'method', 'export_hint']:
            output.append(f"     {key}")
        output.append("")

    return '\n'.join(output)

# ---------------------------------------------------------------------------
# 2. python-modules.md
# ---------------------------------------------------------------------------

def generate_python_modules() -> str:
    root_py_files = [
        'app.py', 'config.py', 'data_loader.py', 'report_builder.py'
    ]

    output = [
        f"# Python Modules Map (generated {TODAY})",
        f"# Root-level modules: classes, methods, top-level functions",
        ""
    ]

    for filename in root_py_files:
        filepath = os.path.join(ROOT, filename)
        if not os.path.exists(filepath):
            continue

        content = read_file_safe(filepath)
        if not content:
            continue

        line_count = len(content.splitlines())
        docstring = extract_module_docstring(content)
        classes = extract_classes(content)
        top_fns = extract_functions(content)

        output.append(f"## {filename}  ({line_count} lines)")
        if docstring:
            output.append(f"   {docstring}")
        output.append("")

        if classes:
            for cls in classes:
                base_str = f"({cls['base']})" if cls['base'] else ""
                output.append(f"   class {cls['name']}{base_str}")
                for m in cls['methods']:
                    output.append(f"     def {pad(m['name'], 28)} ({m['params']})")
                output.append("")

        if top_fns:
            output.append("   Top-level functions:")
            for fn in top_fns:
                output.append(f"     def {pad(fn['name'], 28)} ({fn['params']})")
            output.append("")

    return '\n'.join(output)

# ---------------------------------------------------------------------------
# 3. data-schema.md
# ---------------------------------------------------------------------------

def generate_data_schema() -> str:
    """
    Documents the expected Excel column names and config structure
    by scanning data_loader.py and config.py for Chinese string literals
    and cfg dict keys.
    """
    loader_path = os.path.join(ROOT, 'data_loader.py')
    config_path = os.path.join(ROOT, 'config.py')

    loader_content = read_file_safe(loader_path)
    config_content = read_file_safe(config_path)

    if not loader_content:
        return None

    output = [
        f"# Data Schema Reference (generated {TODAY})",
        f"# Expected Excel sheets, column names, and runtime config keys",
        ""
    ]

    # ── Sheet types ──
    output.append("## Excel Sheets (auto-detected by signature columns)")
    sheet_signatures = [
        ('callback',   ['動作類型', '位置類型', '時間戳'],          'Robot event log (complete, arrive events at stations)'),
        ('station',    ['事件類型', '機器人編號', '位置編號'],        'Labor station robot events (arrived, triggerGo, release)'),
        ('lifecycle',  ['任務全程耗時', '目標位置', '起始位置'],      'Container task lifecycle with duration per stage'),
    ]
    for sheet, cols, desc in sheet_signatures:
        output.append(f"  {pad(sheet, 12)} {desc}")
        for col in cols:
            output.append(f"               • {col}")
    output.append("")

    # ── Key column names ──
    output.append("## Key Column Names (Chinese field names in source data)")
    columns_doc = [
        ('動作類型',              'Action type (complete, etc.) — callback sheet'),
        ('位置類型',              'Location type (LABOR, storage) — callback sheet'),
        ('時間戳',               'Timestamp — all sheets'),
        ('機器人編號',            'Robot ID — station sheet'),
        ('機器人類型',            'Robot model/type (e.g. K50) — station sheet'),
        ('位置編號',              'Location code (LABOR:0:X:Y) — station sheet'),
        ('事件類型',              'Event type (arrived, triggerGo, release, ppReady) — station sheet'),
        ('任務全程耗時(秒)',       'Total task duration in seconds — lifecycle sheet'),
        ('complete(任務完成時間)', 'Task completion timestamp — lifecycle sheet'),
        ('起始位置',              'Source location (HAI-aisle-bay-level-...) — lifecycle sheet'),
        ('目標位置',              'Destination location (LABOR-N) — lifecycle sheet'),
        ('容器編號',              'Container / tote ID — lifecycle sheet'),
        ('*耗時(秒)',             'Stage duration columns (suffix pattern) — lifecycle sheet'),
    ]
    for col, desc in columns_doc:
        output.append(f"  {pad(col, 28)} {desc}")
    output.append("")

    # ── Stage labels map ──
    output.append("## Stage Label Map (Chinese → English, from config.py)")
    stage_map_match = re.search(
        r'STAGE_LABEL_MAP\s*=\s*\{([^}]+)\}', config_content, re.DOTALL
    )
    if stage_map_match:
        for line in stage_map_match.group(1).splitlines():
            line = line.strip().strip(',')
            if line and not line.startswith('#'):
                output.append(f"  {line}")
    else:
        # Fallback: find any dict with Chinese keys
        for line in config_content.splitlines():
            if '→' in line or ('":"' in line and any(ord(c) > 127 for c in line)):
                output.append(f"  {line.strip()}")
    output.append("")

    # ── Runtime cfg dict keys ──
    output.append("## Runtime cfg Dict Keys (built by data_loader.build_config)")
    cfg_keys = [
        ('ws_order',          'list[str]',         'Ordered station names'),
        ('type_map',          'dict[str, str]',     'Station → zone type'),
        ('type_colors',       'dict[str, str]',     'Zone type → hex colour'),
        ('design_rate',       'dict[str, int]',     'Station → target tasks/hr'),
        ('design_total_rate', 'int | None',         'Sum of all design rates'),
        ('point2ws',          'dict[str, str]',     'Location code → station name'),
        ('stages',            'list[str]',          'Lifecycle stage column names'),
        ('stage_lbl',         'list[str]',          'Human-readable stage labels'),
        ('stage_col',         'list[str]',          'Hex colour per stage'),
        ('amr_type',          'str | None',         'Delivery AMR type string, e.g. "K50"'),
    ]
    output.append(f"  {pad('key', 20)} {pad('type', 18)} description")
    output.append(f"  {'-'*20} {'-'*18} {'-'*30}")
    for key, typ, desc in cfg_keys:
        output.append(f"  {pad(key, 20)} {pad(typ, 18)} {desc}")
    output.append("")

    # ── asrs_config.json override ──
    output.append("## asrs_config.json (optional, placed next to .xlsx)")
    output.append("  Keys: station_types, design_rates, type_colors, amr_type")
    output.append('  Example:')
    output.append('    { "station_types": {"LABOR-1": "Zone A"},')
    output.append('      "design_rates":  {"LABOR-1": 120},')
    output.append('      "type_colors":   {"Zone A": "#ff6b6b"},')
    output.append('      "amr_type":      "K50" }')
    output.append("")

    # ── Location code format ──
    output.append("## Location Code Formats")
    output.append("  LABOR station:   LABOR:0:<x>:<y>   (grouped by Y coord into zones A,B,C,...)")
    output.append("  Storage grid:    HAI-<aisle>-<bay>-<level>-<col>")
    output.append("  Destination:     LABOR-<N>          (matches station record label)")
    output.append("")

    return '\n'.join(output)

# ---------------------------------------------------------------------------
# 4. docs-index.md
# ---------------------------------------------------------------------------

def generate_docs_index() -> str:
    md_files = sorted(f for f in os.listdir(ROOT) if f.endswith('.md'))
    ai_codex_files = []
    if os.path.exists(OUTPUT_DIR):
        ai_codex_files = sorted(os.listdir(OUTPUT_DIR))

    if not md_files and not ai_codex_files:
        return None

    output = [
        f"# Documentation Registry (generated {TODAY})",
        ""
    ]

    if md_files:
        output.append("## Markdown files in root/")
        for md in md_files:
            output.append(f"  - {md}")
        output.append("")

    if ai_codex_files:
        output.append("## AI-Codex index files (.ai-codex/)")
        for f in ai_codex_files:
            if f != 'docs-index.md':
                output.append(f"  - .ai-codex/{f}")
        output.append("")

    output.append("## Output directory")
    output.append("  asrs_analysis_output/{YYYY-MM-DD}/")
    output.append("    asrs_analysis_report.html  — main deliverable (self-contained)")
    output.append("    throughput_by_workstation_hour.xlsx")
    output.append("    cycle_time_distribution.xlsx")
    output.append("    retrieval_demand_by_aisle.xlsx")
    output.append("    retrieval_demand_by_bay.xlsx")
    output.append("    robot_dwell_intervals.xlsx")
    output.append("    robot_switch_time_intervals.xlsx")
    output.append("")

    return '\n'.join(output)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print('\nai-codex -- ESS Analyzer Indexer\n')

    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except Exception as e:
        print(f"Error: could not create output directory \"{OUTPUT_DIR}\": {e}")
        return

    generators = [
        ('analyses-index.md',   generate_analyses_index),
        ('python-modules.md',   generate_python_modules),
        ('data-schema.md',      generate_data_schema),
        ('docs-index.md',       generate_docs_index),
    ]

    total_files = 0
    total_lines = 0

    for filename, generator in generators:
        try:
            content = generator()
        except Exception as e:
            print(f"  {pad(filename, 25)} ERROR: {e}")
            continue

        if not content:
            print(f"  {pad(filename, 25)} skipped (no content)")
            continue

        line_count = len(content.split('\n'))
        total_lines += line_count
        total_files += 1

        out_path = os.path.join(OUTPUT_DIR, filename)
        try:
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            print(f"  {pad(filename, 25)} ERROR writing: {e}")
            continue

        print(f"  {pad(filename, 25)} {line_count} lines  ->  {out_path}")

    print(f"\n  Total: {total_lines} lines across {total_files} files")
    print(f"  Output: {os.path.relpath(OUTPUT_DIR, ROOT)}/\n")


if __name__ == '__main__':
    main()
