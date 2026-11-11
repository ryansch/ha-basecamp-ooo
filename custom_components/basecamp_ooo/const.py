"""Constants for the Basecamp Out of Office integration."""

DOMAIN = "basecamp_ooo"

CONF_ACCOUNT_ID = "account_id"
CONF_BUCKET_ID = "bucket_id"
CONF_SCHEDULE_ID = "schedule_id"
CONF_PERSON_ID = "person_id"
CONF_RUN_TIME = "run_time"

DEFAULT_RUN_TIME = "06:00:00"

OAUTH_AUTHORIZE = "https://launchpad.37signals.com/authorization/new"
OAUTH_TOKEN = "https://launchpad.37signals.com/authorization/token"
LAUNCHPAD_IDENTITY = "https://launchpad.37signals.com/authorization.json"

API_BASE = "https://3.basecampapi.com/{account_id}"

USER_AGENT = "Home Assistant basecamp_ooo (https://github.com/ryansch/ha-basecamp-ooo)"

SCHEDULE_URL_EXAMPLE = "https://3.basecamp.com/1234567/buckets/2345678/schedules/3456789"
