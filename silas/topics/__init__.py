"""Topics: topic detection, goals, proactivity, scheduler, and triggers."""

# Topic matching (original topics/)
from silas.topics.matcher import TriggerMatcher
from silas.topics.model import EventSubscription, ReportingConfig, Topic, TriggerSpec, SoftTrigger, ApprovalSpec
from silas.topics.parser import parse_topic
from silas.topics.registry import TopicRegistry

# Re-export supporting goal types that Topics may reference
from silas.models.goals import GoalSchedule, Schedule, StandingApproval

# Proactivity (merged from proactivity/)
from silas.topics.calibrator import SimpleAutonomyCalibrator
from silas.topics.proactivity_fatigue import ApprovalFatigueTracker
from silas.topics.proactivity_preferences import PreferenceInferenceEngine
from silas.topics.suggestions import SimpleSuggestionEngine
from silas.topics.ux_metrics import UXMetricsCollector

# Scheduler (merged from scheduler/)
from silas.topics.scheduler import SilasScheduler

__all__ = [
    # topics
    "ApprovalSpec",
    "EventSubscription",
    "ReportingConfig",
    "SoftTrigger",
    "Topic",
    "TopicRegistry",
    "TriggerMatcher",
    "TriggerSpec",
    "parse_topic",
    # goal/schedule support types (live on Topic)
    "GoalSchedule",
    "Schedule",
    "StandingApproval",
    # proactivity
    "ApprovalFatigueTracker",
    "PreferenceInferenceEngine",
    "SimpleAutonomyCalibrator",
    "SimpleSuggestionEngine",
    "UXMetricsCollector",
    # scheduler
    "SilasScheduler",
]
