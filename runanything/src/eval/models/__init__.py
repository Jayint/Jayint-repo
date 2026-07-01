"""Evaluation models for different baseline approaches.

Keep imports lazy so the offline DockerAgent runner does not need optional
dependencies from unrelated baselines.
"""

__all__ = ["RATModel", "SWEAgentModel", "PipreqsModel", "Repo2RunModel", "DockerAgentModel"]


def __getattr__(name):
    if name == "RATModel":
        from .rat_model import RATModel

        return RATModel
    if name == "SWEAgentModel":
        from .sweagent_model import SWEAgentModel

        return SWEAgentModel
    if name == "PipreqsModel":
        from .pipreqs_model import PipreqsModel

        return PipreqsModel
    if name == "Repo2RunModel":
        from .repo2run_model import Repo2RunModel

        return Repo2RunModel
    if name == "DockerAgentModel":
        from .dockeragent_model import DockerAgentModel

        return DockerAgentModel
    raise AttributeError(name)
