# Basecamp Out of Office

A Home Assistant custom integration that turns on your Basecamp out-of-office on
the first morning of a stretch of time off, using the all-day entries on a
Basecamp calendar (schedule).

Setup happens in the Home Assistant UI: a config flow that signs in to Basecamp
with OAuth through Application Credentials. There is no YAML configuration.

## Setup

### 1. Register a Launchpad app

1. Go to <https://launchpad.37signals.com/integrations> and register a new
   integration (any name, e.g. "Home Assistant").
2. Set the **Redirect URI** to:

   ```
   https://my.home-assistant.io/redirect/oauth
   ```

3. Keep the **Client ID** and **Client Secret** handy for step 3.

### 2. Install the integration

1. In Home Assistant, open the **Marketplace**, add
   `https://github.com/ryansch/ha-basecamp-ooo` as a custom repository
   (type: Integration).
2. Install **Basecamp Out of Office** and restart Home Assistant.

### 3. Add the integration

1. Go to **Settings > Devices & services > Add integration** and pick
   **Basecamp Out of Office**.
2. When asked for application credentials, enter the Client ID and Client
   Secret from step 1.
3. Sign in to Basecamp and authorize the app.
4. If your login has more than one Basecamp account, choose the account.
5. Paste the URL of the schedule that holds your time-off entries, e.g.
   `https://3.basecamp.com/1234567/buckets/2345678/schedules/3456789`. The
   schedule must belong to the account you chose.

### Options

**Daily run time** (default `06:00`, Home Assistant's local time zone): when
the daily check runs. Change it under the integration's **Configure** button.

## How it works

Once a day, at the run time, the integration reads the schedule, works out your
stretches of time off, and decides whether to set out-of-office. It also runs
once each time Home Assistant starts (or the integration loads), so a morning
missed while Home Assistant was down is caught up. The **Run now** button runs
the same check on demand.

### What counts as a day off

- An entry counts when **you are a participant**. The title and the creator
  don't matter.
- **All-day entries only.** Timed entries are ignored.
- **Recurring entries are ignored.**

### Stretches

- Consecutive days off merge into one stretch.
- A weekend between two days off joins them: Friday off plus Monday off is one
  Friday-to-Monday stretch, and out-of-office is set on Friday morning, ending
  Monday.
- A stretch that ends on a Friday still ends on Friday; the weekend after it is
  not added.

### When it sets out-of-office

- Only on the **first morning of a stretch**. Running on a later day of the same
  stretch does nothing.
- It sets out-of-office from the stretch's first day to its last day.
- It **never overwrites or replaces an existing out-of-office**, whether it is
  active now or set for a future date. If one exists, the run leaves it alone.
- It **never clears out-of-office**. Basecamp turns it off by itself after the
  end date.
- It never sets out-of-office ahead of time; it only acts on the morning time
  off starts.

Anything you set by hand in Basecamp (a sick day, an early finish) stays
yours: the integration won't change or remove it.

## Entities

All entities belong to one service device, **Basecamp Out of Office**. Default
entity IDs:

| Entity | Description |
| --- | --- |
| `sensor.basecamp_out_of_office_next_time_off` | Start date of the current or next stretch of time off. Attributes: `end` (last day of the stretch), `entry_ids` (the schedule entries that make it up). |
| `sensor.basecamp_out_of_office_last_run` | Time of the last run. Attributes: `result`, `message` (human-readable summary), `set_start` / `set_end` (dates set by that run, if any). |
| `binary_sensor.basecamp_out_of_office_active` | On while Basecamp out-of-office is enabled and covers today. |
| `button.basecamp_out_of_office_run_now` | Runs the daily check immediately. |

The out-of-office state behind the binary sensor is refreshed every 30 minutes,
so changes you make in Basecamp show up without waiting for the next daily run.

### Last-run results

| `result` | Meaning |
| --- | --- |
| `no_time_off_today` | Today isn't part of any stretch. |
| `mid_stretch` | Today is in a stretch but not its first day; nothing to do. |
| `already_active` | A stretch starts today and out-of-office already covers today. |
| `skipped_existing_future` | A stretch starts today, but an out-of-office is already set for another period; it was left in place. |
| `set` | Out-of-office was set for the stretch. |
| `error` | The run failed; see `message`. |

## Troubleshooting

- **Failed runs** raise a repair issue, "Basecamp out-of-office run failed",
  under **Settings > Repairs**, with the error message. It clears itself after
  the next successful run.
- **Authorization failures** (revoked access, expired refresh token) start a
  reauthentication flow: Home Assistant shows the integration as needing
  attention, and you sign in to Basecamp again. Sign in as the same Basecamp
  person; a different person is rejected.

## Development

Set up the virtualenv (Python 3.14, via Flox):

```sh
flox activate -- sh -c 'uv venv --python python3.14 .venv && uv pip install --python .venv/bin/python -r requirements_test.txt'
```

Run the tests:

```sh
.venv/bin/pytest
```

Notes:

- Tests currently run against Home Assistant 2026.10.4, because
  `pytest-homeassistant-custom-component` has no 2026.11 release yet. The
  integration itself targets 2026.11 (see `hacs.json`).
- `tests/conftest.py` imports `custom_components.basecamp_ooo` at module level.
  That import beats the `custom_components` package bundled with
  `pytest-homeassistant-custom-component`, so Home Assistant's loader finds this
  integration. Don't remove it, and don't add a `custom_components/__init__.py`.
