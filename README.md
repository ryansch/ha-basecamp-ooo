# Basecamp Out of Office

A Home Assistant custom integration that turns on your Basecamp out-of-office on
the first day of a stretch of time off, using the all-day entries on a
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

3. Optionally, upload [`assets/icon.png`](assets/icon.png) as the app's icon.
4. Keep the **Client ID** and **Client Secret** handy for step 3.

### 2. Install the integration

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=ryansch&repository=ha-basecamp-ooo&category=integration)

The button opens this repository in your Home Assistant's Marketplace; then
install it (item 3 below). Or add it by hand:

1. In Home Assistant, open the **Marketplace** and select **Add from link**
   (top right).
2. Enter `ryansch/ha-basecamp-ooo` (or
   `https://github.com/ryansch/ha-basecamp-ooo`) and select **Add**. If
   asked for a category, choose **Integration**.
3. Install **Basecamp Out of Office**. No restart is needed.

### 3. Set up the integration

1. Select **Set up** on the integration's Marketplace page. (Later, or if you
   closed that page, go to **Settings > Devices & services > Add integration**
   and pick **Basecamp Out of Office**.)
2. When asked for application credentials, enter the Client ID and Client
   Secret from step 1. **Name** is only a label for this credential pair in
   Home Assistant; anything works, e.g. "Basecamp".
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
once each time Home Assistant starts (or the integration loads), so a run
missed while Home Assistant was down is caught up. The **Run now** button runs
the same check on demand. Both act at any time on the first day of a stretch.

If a run fails (for example, Basecamp is down), it is retried later the same
day: after 5 minutes, then 15, then every 30 minutes until midnight in Home
Assistant's time zone. Authorization failures are not retried; they start
reauthentication instead (see Troubleshooting).

### What counts as a day off

- An entry counts when **you are a participant**. The title and the creator
  don't matter.
- **All-day entries only.** Timed entries are ignored.
- **Recurring entries are ignored.**

### Stretches

- Consecutive days off merge into one stretch.
- A weekend between two days off joins them: Friday off plus Monday off is one
  Friday-to-Monday stretch, and out-of-office is set on Friday, ending
  Monday.
- A stretch that ends on a Friday still ends on Friday; the weekend after it is
  not added.

### When it sets out-of-office

- Only on the **first day of a stretch**. Running on a later day of the same
  stretch does nothing.
- It sets out-of-office from the stretch's first day to its last day.
- It **never overwrites or replaces an existing out-of-office**, whether it is
  active now or set for a future date. If one exists, the run leaves it alone.
  An out-of-office whose end date has already passed doesn't count.
- It **never clears out-of-office**. Basecamp turns it off by itself after the
  end date.
- It never sets out-of-office ahead of time; it only acts on the day time off
  starts.
- It sets each stretch **only once**. If you turn off an out-of-office it set,
  it stays off: a later run that day (Run now, a retry, a restart, or a reload)
  won't set it again.

Anything you set by hand in Basecamp (a sick day, an early finish) stays
yours: the integration won't change or remove it.

## Entities

All entities belong to one service device, **Basecamp OOO**. Default entity
IDs:

| Entity | Description |
| --- | --- |
| `sensor.basecamp_ooo_next_time_off` | Start date of the current or next stretch of time off. Attributes: `end` (last day of the stretch), `entry_ids` (the schedule entries that make it up). |
| `sensor.basecamp_ooo_last_run` | Time of the last run. Attributes: `result`, `message` (human-readable summary), `set_start` / `set_end` (dates set by that run, if any). |
| `binary_sensor.basecamp_ooo_active` | On while Basecamp out-of-office is enabled and covers today. |
| `button.basecamp_ooo_run_now` | Runs the daily check immediately. |

The out-of-office state behind the binary sensor is refreshed every 30 minutes,
so changes you make in Basecamp show up without waiting for the next daily run.

### Last-run results

| `result` | Meaning |
| --- | --- |
| `no_time_off_today` | Today isn't part of any stretch. |
| `mid_stretch` | Today is in a stretch but not its first day; nothing to do. |
| `already_active` | A stretch starts today and out-of-office already covers today, or the integration already set it for this stretch (and it isn't set again). |
| `skipped_existing_future` | A stretch starts today, but an out-of-office is already set for another period; it was left in place. |
| `set` | Out-of-office was set for the stretch. |
| `error` | The run failed; see `message`. |

## Troubleshooting

- **Failed runs** raise a repair issue, "Basecamp out-of-office run failed",
  under **Settings > Repairs**, with the error message. The run is retried
  later the same day, and the repair clears itself after the next successful
  run.
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

- Tests use [`pytest-homeassistant`](https://github.com/home-assistant/core/tree/dev/packages/pytest-homeassistant),
  Home Assistant's own test package. Its version pins the Home Assistant
  version the tests run against (currently 2026.11.1); upgrade them together.
  Don't install `pytest-homeassistant-custom-component` alongside it.
