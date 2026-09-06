"""Jarvis for Hermes Agent: intelligence, memory, experience and self-evolution."""
__version__ = "5.0.0"

from .config import CONFIG
from .core import JarvisEngine
from .evolution import EvolutionEngine
from .experience_store import ExperienceStore
from .gateway_supervisor import GatewaySupervisor
from .hermes_memory_provider import JarvisMemoryProvider
from .hermes_plugin import JarvisPluginRuntime
from .intelligence import JarvisIntelligence, RoutingDecision
from .tencent_memory import TencentMemoryClient
from .tools import AutonomousExecutor, WorkflowPlanner, WorkflowProgressRenderer
from .workflow_store import WorkflowStore

__all__ = [
    "CONFIG",
    "JarvisEngine",
    "EvolutionEngine",
    "ExperienceStore",
    "GatewaySupervisor",
    "JarvisMemoryProvider",
    "JarvisPluginRuntime",
    "JarvisIntelligence",
    "RoutingDecision",
    "TencentMemoryClient",
    "AutonomousExecutor",
    "WorkflowPlanner",
    "WorkflowProgressRenderer",
    "WorkflowStore",
]
