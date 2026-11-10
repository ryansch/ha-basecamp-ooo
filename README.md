# Basecamp Out of Office

A Home Assistant custom integration that sets your Basecamp out-of-office status
on the first day of time off found on a Basecamp calendar (schedule).

Setup is through the Home Assistant UI only (config flow with Basecamp OAuth via
Application Credentials). There is no YAML configuration.

## Installation

Add this repository as a custom repository in the Home Assistant Marketplace
(HACS-compatible), install **Basecamp Out of Office**, and restart Home Assistant.

## Development

```sh
flox activate
uv venv --python python3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/pytest -v
```
