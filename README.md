# TASI Analyst Consensus Intelligence Agent

Monitors research firms publishing opinions on Tadawul-listed companies,
extracts and validates their recommendations, and highlights stocks supported
by **at least two independent research firms**.

This is a research-intelligence system, not a predictor. It does not forecast
prices, does not generate its own Buy/Sell calls, and does not treat one
analyst's view as consensus.

## Quick start

```bash
pip install -r requirements.txt
python run.py init
python run.py demo        # synthetic data, clearly banner-flagged
python run.py report      # output/tasi_analyst_report_<date>.html
python run.py dashboard