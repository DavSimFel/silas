"""Topics: topic detection, goals, proactivity, scheduler, and triggers."""

# Topic matching (original topics/)
from silas.topics.matcher import TriggerMatcher
from silas.topics.model import Topic
from silas.topics.parser import parse_topic
from silas.topics.registry import TopicRegistry

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
    "Topic",
    "TopicRegistry",
    "TriggerMatcher",
    "parse_topic",
    # proactivity
    "ApprovalFatigueTracker",
    "PreferenceInferenceEngine",
    "SimpleAutonomyCalibrator",
    "SimpleSuggestionEngine",
    "UXMetricsCollector",
    # scheduler
    "SilasScheduler",
]
