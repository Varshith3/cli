from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_kernel_load import load_scenario_contract, load_topology_contract
from platform_cli.orchestrate_kernel.runtime_support import write_json, write_markdown
from platform_cli.tools.ai_provider import detect_provider_statuses, generate_text
from platform_cli.tools.orchestrate_contract import load_agent_contract

from platform_cli.orchestrate_kernel.provider_adapters import ProviderAdapterResolution, resolve_provider_adapter


@dataclass
class PlannedAgentPacket:
    agent_id: str
    mode: str
    allowed_skills: List[str]
    allowed_plugins: List[str]
    produces_artifacts: List[str]
    prompt: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioExecutionResult:
    scenario_id: str
    provider_plugin: str
    effective_plugin: str
    requested_host: str
    effective_host: str
    effective_provider: str
    fallback_used: bool
    execution_waves: List[List[str]]
    packets: List[PlannedAgentPacket]
    executed: bool
    outputs: List[Dict[str, str]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "provider_plugin": self.provider_plugin,
            "effective_plugin": self.effective_plugin,
            "requested_host": self.requested_host,
            "effective_host": self.effective_host,
            "effective_provider": self.effective_provider,
            "fallback_used": self.fallback_used,
            "execution_waves": [list(wave) for wave in self.execution_waves],
            "packets": [packet.to_dict() for packet in self.packets],
            "executed": self.executed,
            "outputs": list(self.outputs),
        }


def run_subagent_scenario(
    *,
    scenario_id: str,
    repo_root: Path | None = None,
    execute_provider: bool = False,
) -> ScenarioExecutionResult:
    resolved_root = resolve_repo_root(repo_root)
    scenario = load_scenario_contract(scenario_id=scenario_id, repo_root=resolved_root)
    topology = load_topology_contract(repo_root=resolved_root)
    adapter = resolve_provider_adapter(
        provider_plugin_id=str(scenario.get("provider_plugin", "")).strip(),
        requested_host=str(scenario.get("host_mode", "")).strip(),
        repo_root=resolved_root,
    )

    packets = build_agent_packets(
        repo_root=resolved_root,
        requested_agents=_normalize_list(scenario.get("requested_agents", [])),
        prompt_brief=_normalize_list(scenario.get("prompt_brief", [])),
        topology=topology,
        effective_plugin=adapter.effective_plugin,
    )
    packet_index = {packet.agent_id: index for index, packet in enumerate(packets)}
    execution_waves = plan_execution_waves(
        requested_agents=[packet.agent_id for packet in packets],
        topology=topology,
    )
    outputs: List[Dict[str, str]] = []
    if execute_provider:
        if not adapter.available:
            raise PlatformError(
                f"Provider '{adapter.effective_provider}' is not available for scenario execution: {adapter.detail}",
                code="E_ORCHESTRATE_PROVIDER_UNAVAILABLE",
                reason=adapter.effective_provider,
            )
        statuses = detect_provider_statuses(refresh=False)
        output_map: Dict[str, str] = {}
        for wave in execution_waves:
            wave_packets = [packets[packet_index[agent_id]] for agent_id in wave if agent_id in packet_index]
            if len(wave_packets) <= 1:
                packet = wave_packets[0]
                output_map[packet.agent_id] = _generate_packet_output(
                    packet=packet,
                    provider=adapter.effective_provider,
                    statuses=statuses,
                    model=adapter.model or None,
                )
                continue

            with ThreadPoolExecutor(max_workers=len(wave_packets)) as executor:
                future_map = {
                    executor.submit(
                        _generate_packet_output,
                        packet=packet,
                        provider=adapter.effective_provider,
                        statuses=statuses,
                        model=adapter.model or None,
                    ): packet.agent_id
                    for packet in wave_packets
                }
                for future in as_completed(future_map):
                    agent_id = future_map[future]
                    output_map[agent_id] = future.result()

        outputs = [{"agent_id": packet.agent_id, "output": output_map.get(packet.agent_id, "")} for packet in packets]

    return ScenarioExecutionResult(
        scenario_id=str(scenario.get("id", "")).strip(),
        provider_plugin=adapter.provider_plugin,
        effective_plugin=adapter.effective_plugin,
        requested_host=adapter.requested_host,
        effective_host=adapter.effective_host,
        effective_provider=adapter.effective_provider,
        fallback_used=adapter.fallback_used,
        execution_waves=execution_waves,
        packets=packets,
        executed=execute_provider,
        outputs=outputs,
    )


def persist_scenario_result(*, run_root: Path, result: ScenarioExecutionResult) -> None:
    write_json(
        run_root / "subagent_execution_plan.json",
        {
            "scenario_id": result.scenario_id,
            "provider_plugin": result.provider_plugin,
            "effective_plugin": result.effective_plugin,
            "requested_host": result.requested_host,
            "effective_host": result.effective_host,
            "effective_provider": result.effective_provider,
            "fallback_used": result.fallback_used,
            "execution_waves": [list(wave) for wave in result.execution_waves],
            "packets": [packet.to_dict() for packet in result.packets],
            "executed": result.executed,
        },
    )
    write_json(run_root / "subagent_execution_result.json", result.to_dict())
    lines = [
        "# Sub-Agent Prompt Packets",
        "",
        f"- Scenario: `{result.scenario_id}`",
        f"- Provider plugin: `{result.provider_plugin}`",
        f"- Effective provider plugin: `{result.effective_plugin}`",
        f"- Requested host: `{result.requested_host}`",
        f"- Effective host: `{result.effective_host}`",
        f"- Effective provider: `{result.effective_provider}`",
        f"- Fallback used: `{str(result.fallback_used).lower()}`",
        f"- Execution waves: `{result.execution_waves}`",
        "",
    ]
    for packet in result.packets:
        lines.extend(
            [
                f"## {packet.agent_id}",
                "",
                f"- Mode: `{packet.mode}`",
                f"- Allowed skills: {', '.join(f'`{item}`' for item in packet.allowed_skills) or '(none)'}",
                f"- Allowed plugins: {', '.join(f'`{item}`' for item in packet.allowed_plugins) or '(none)'}",
                f"- Produces artifacts: {', '.join(f'`{item}`' for item in packet.produces_artifacts) or '(none)'}",
                "",
                "```text",
                packet.prompt,
                "```",
                "",
            ]
        )
    write_markdown(run_root / "subagent_prompt_packets.md", lines)


def build_agent_packets(
    *,
    repo_root: Path,
    requested_agents: Sequence[str],
    prompt_brief: Sequence[str],
    topology: Dict[str, Any],
    effective_plugin: str | None = None,
) -> List[PlannedAgentPacket]:
    parallel_groups = topology.get("parallel_groups", [])
    sequential_groups = topology.get("sequential_groups", [])
    if not isinstance(parallel_groups, list) or not isinstance(sequential_groups, list):
        raise PlatformError(
            "Topology contract must define list-valued parallel_groups and sequential_groups.",
            code="E_ORCHESTRATE_TOPOLOGY_INVALID",
            reason="groups",
        )

    group_modes: Dict[str, str] = {}
    for group in list(parallel_groups) + list(sequential_groups):
        if not isinstance(group, dict):
            continue
        mode = str(group.get("mode", "sequential")).strip() or "sequential"
        for agent_id in _normalize_list(group.get("agents", [])):
            group_modes[agent_id] = mode

    packets: List[PlannedAgentPacket] = []
    for agent_id in requested_agents:
        contract = load_agent_contract(agent_id=agent_id, repo_root=repo_root)
        allowed_plugins = _normalize_list(contract.get("allowed_plugins", []))
        produces_artifacts = _normalize_list(contract.get("produces_artifacts", []))
        if effective_plugin and effective_plugin not in allowed_plugins:
            raise PlatformError(
                f"Agent '{agent_id}' does not allow provider plugin '{effective_plugin}'.",
                code="E_ORCHESTRATE_AGENT_PLUGIN_MISMATCH",
                reason=agent_id,
            )
        prompt_lines = [
            f"You are the repo-defined sub-agent '{agent_id}'.",
            f"Role: {str(contract.get('role', '')).strip()}",
            "Use only the allowed skills and plugins below.",
            "Return a short execution-ready assessment for this scenario in 3 bullets.",
            "",
            "Scenario brief:",
            *[f"- {line}" for line in prompt_brief],
            "",
            "Prompt contract:",
            *[f"- {line}" for line in _normalize_list(contract.get('prompt_contract', []))],
            "",
            "Allowed skills:",
            *[f"- {line}" for line in _normalize_list(contract.get('allowed_skills', []))],
            "",
            "Allowed plugins:",
            *[f"- {line}" for line in allowed_plugins],
            "",
            "Produces artifacts:",
            *[f"- {line}" for line in produces_artifacts],
        ]
        packets.append(
            PlannedAgentPacket(
                agent_id=agent_id,
                mode=group_modes.get(agent_id, "sequential"),
                allowed_skills=_normalize_list(contract.get("allowed_skills", [])),
                allowed_plugins=allowed_plugins,
                produces_artifacts=produces_artifacts,
                prompt="\n".join(prompt_lines).strip(),
            )
        )
    return packets


def plan_execution_waves(
    *,
    requested_agents: Sequence[str],
    topology: Dict[str, Any],
) -> List[List[str]]:
    execution_waves = topology.get("execution_waves", [])
    parallel_groups = topology.get("parallel_groups", [])
    sequential_groups = topology.get("sequential_groups", [])
    requested = [agent_id for agent_id in requested_agents if agent_id]

    waves: List[List[str]] = []
    consumed: set[str] = set()
    if isinstance(execution_waves, list) and execution_waves:
        for group in execution_waves:
            if not isinstance(group, dict):
                continue
            wave = [agent_id for agent_id in _normalize_list(group.get("agents", [])) if agent_id in requested and agent_id not in consumed]
            if wave:
                waves.append(wave)
                consumed.update(wave)
    for group in parallel_groups:
        if not isinstance(group, dict):
            continue
        wave = [agent_id for agent_id in _normalize_list(group.get("agents", [])) if agent_id in requested and agent_id not in consumed]
        if wave:
            waves.append(wave)
            consumed.update(wave)
    for group in sequential_groups:
        if not isinstance(group, dict):
            continue
        for agent_id in _normalize_list(group.get("agents", [])):
            if agent_id in requested and agent_id not in consumed:
                waves.append([agent_id])
                consumed.add(agent_id)

    for agent_id in requested:
        if agent_id in consumed:
            continue
        waves.append([agent_id])
        consumed.add(agent_id)

    return waves


def _generate_packet_output(
    *,
    packet: PlannedAgentPacket,
    provider: str,
    statuses: Dict[str, Any],
    model: str | None,
) -> str:
    return generate_text(
        provider=provider,
        statuses=statuses,
        prompt=packet.prompt,
        model=model,
    ).strip()


def _normalize_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
