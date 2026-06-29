"""
analyses/ — one module per domain engine.

Each module exposes a single public function:
    run(data: dict, cfg: dict) -> list[ChartResult]

ChartResult keys
----------------
id          : str          unique chart identifier (snake_case)
title       : str          human-readable chart title
figure      : go.Figure    interactive Plotly figure
source      : str          which sheet(s) the data came from
method      : str          brief description of the computation
export_hint : str          filename of any associated Excel export (may be "")
"""
