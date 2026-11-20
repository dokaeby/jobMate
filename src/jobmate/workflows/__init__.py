from .dev_review import SPEC as DEV_REVIEW_SPEC
from .gmail_calendar import SPEC as GMAIL_CALENDAR_SPEC
from .skill_radar import SPEC as SKILL_RADAR_SPEC

WORKFLOWS = {
    DEV_REVIEW_SPEC.key: DEV_REVIEW_SPEC,
    GMAIL_CALENDAR_SPEC.key: GMAIL_CALENDAR_SPEC,
    SKILL_RADAR_SPEC.key: SKILL_RADAR_SPEC,
}
