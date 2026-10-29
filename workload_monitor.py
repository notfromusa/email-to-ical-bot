"""
Workload and System Usage Monitor for iCal Bot

Monitors workstation GPU memory (VRAM), GPU utilization, and CPU load to ensure
iCal Bot's LLM inference yields immediately to other user or colleague workloads.
Provides automated model unloading from VRAM via Ollama APIs and manages backoff
cooldown periods before resuming inference.

References:
    - NVIDIA System Management Interface (nvidia-smi) documentation:
      https://developer.nvidia.com/system-management-interface
    - Ollama API Documentation (model lifecycle & keep_alive parameter):
      https://github.com/ollama/ollama/blob/main/docs/api.md
    - Linux /proc/stat documentation (kernel CPU statistics):
      proc(5) man page, kernel documentation on /proc/stat
"""

import os
import re
import sys
import time
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set

import requests

import config

logger = logging.getLogger(__name__)

# Default whitelist of processes always allowed to use GPU without triggering preemption
# (desktop environments, display servers, IDEs, browsers, audio/video tools)
DEFAULT_WHITELISTED_PROCS = [
    "xorg",
    "xwayland",
    "gnome-shell",
    "gsd-color",
    "gnome-control-c",
    "gnome-settings-",
    "mutter",
    "kwin",
    "plasmashell",
    "sddm",
    "gdm",
    "lightdm",
    "colord",
    "chrome",
    "chromium",
    "firefox",
    "electron",
    "slack",
    "code",
    "vscode",
    "discord",
    "teams",
    "zoom",
    "obs",
    "spotify",
    "nautilus",
    "x-terminal-emulator",
    "antigravity-ide",
    "unsloth-studio",
    "ciscocollabhost",
    "xdg-desktop-portal-gnome",
    "nextcloud",
    "obsidian",
]

# Patterns representing Ollama's own runner / server processes
OLLAMA_PROC_PATTERNS = [
    "ollama",
    "llama_server",
    "llama-server",
    "ollama_llama_server",
    "runner",
    "ggml",
]


class WorkloadYieldError(Exception):
    """Exception raised when an LLM operation cannot run because the system is busy or in cooldown."""

    def __init__(self, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Initialize WorkloadYieldError.

        Args:
            message: Descriptive explanation of why the LLM is yielding.
            metadata: Diagnostic context dictionary.
        """
        super().__init__(message)
        self.metadata = metadata or {}


class WorkloadMonitor:
    """Monitors system GPU and CPU load and manages LLM yielding and cooldowns."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        vram_threshold_mb: Optional[int] = None,
        gpu_util_threshold: Optional[int] = None,
        cpu_threshold_pct: Optional[int] = None,
        cpu_sustained_polls: Optional[int] = None,
        cooldown_duration: Optional[int] = None,
        whitelisted_procs: Optional[List[str]] = None,
    ) -> None:
        """Initialize the WorkloadMonitor.

        Args:
            enabled: Whether workload monitoring is active.
            vram_threshold_mb: VRAM usage threshold in MiB for non-whitelisted processes.
            gpu_util_threshold: GPU utilization percentage threshold.
            cpu_threshold_pct: CPU percentage threshold for sustained load.
            cpu_sustained_polls: Consecutive checks exceeding CPU threshold before triggering.
            cooldown_duration: Cooldown duration in seconds following preemption or timeout.
            whitelisted_procs: Custom list of whitelisted process names.
        """
        self.enabled = (
            getattr(config, "WORKLOAD_MONITOR_ENABLED", True)
            if enabled is None
            else bool(enabled)
        )
        self.vram_threshold_mb = (
            getattr(config, "WORKLOAD_VRAM_THRESHOLD_MB", 500)
            if vram_threshold_mb is None
            else int(vram_threshold_mb)
        )
        self.gpu_util_threshold = (
            getattr(config, "WORKLOAD_GPU_UTIL_THRESHOLD", 30)
            if gpu_util_threshold is None
            else int(gpu_util_threshold)
        )
        self.cpu_threshold_pct = (
            getattr(config, "WORKLOAD_CPU_THRESHOLD_PCT", 70)
            if cpu_threshold_pct is None
            else int(cpu_threshold_pct)
        )
        self.cpu_sustained_polls = (
            getattr(config, "WORKLOAD_CPU_SUSTAINED_POLLS", 3)
            if cpu_sustained_polls is None
            else int(cpu_sustained_polls)
        )
        self.cooldown_duration = (
            getattr(config, "WORKLOAD_COOLDOWN_SECONDS", 300)
            if cooldown_duration is None
            else int(cooldown_duration)
        )

        configured_whitelist = getattr(config, "WORKLOAD_WHITELISTED_PROCS", "") or ""
        whitelist_list = list(DEFAULT_WHITELISTED_PROCS)
        if configured_whitelist:
            for item in configured_whitelist.split(","):
                cleaned = item.strip().lower()
                if cleaned and cleaned not in whitelist_list:
                    whitelist_list.append(cleaned)
        if whitelisted_procs:
            for p in whitelisted_procs:
                cleaned = p.strip().lower()
                if cleaned and cleaned not in whitelist_list:
                    whitelist_list.append(cleaned)
        self.whitelisted_procs = whitelist_list

        self._cooldown_until: float = 0.0
        self._cooldown_reason: str = ""
        self._last_offending_workload: Optional[str] = None
        self._cpu_high_count: int = 0
        self._prev_cpu_total: int = 0
        self._prev_cpu_idle: int = 0
        self._llm_active: bool = False

    def set_llm_active(self, active: bool) -> None:
        """Mark whether local LLM inference is actively in-flight.

        Args:
            active: True when inference is executing, False when idle.
        """
        self._llm_active = bool(active)

    def is_whitelisted(self, proc_name: str) -> bool:
        """Check whether a process name matches the whitelist.

        Args:
            proc_name: Process name or path.

        Returns:
            bool: True if process is whitelisted, False otherwise.
        """
        if not proc_name:
            return True
        basename = os.path.basename(proc_name).lower()
        proc_lower = proc_name.lower()
        for pattern in self.whitelisted_procs:
            pat = pattern.lower()
            if pat in basename or pat in proc_lower:
                return True
        return False

    def is_ollama_process(self, proc_name: str) -> bool:
        """Check whether a process is Ollama or its internal llama-server runner.

        Args:
            proc_name: Process name or path.

        Returns:
            bool: True if process is Ollama, False otherwise.
        """
        if not proc_name:
            return False
        basename = os.path.basename(proc_name).lower()
        return any(pattern in basename for pattern in OLLAMA_PROC_PATTERNS)

    def detect_colleague_gpu_activity(self) -> Optional[str]:
        """Detect any non-whitelisted process consuming GPU VRAM above threshold.

        Returns:
            Optional[str]: Description of offending process, or None if clear.
        """
        if not shutil.which("nvidia-smi"):
            return None

        try:
            cmd = [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return None

            for line in res.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 3:
                    continue
                pid, name, used_mem_str = parts[0], parts[1], parts[2]
                try:
                    used_mem = int(used_mem_str)
                except ValueError:
                    continue

                if self.is_whitelisted(name):
                    continue
                if self.is_ollama_process(name):
                    continue

                if used_mem >= self.vram_threshold_mb:
                    base = os.path.basename(name) or name
                    offending = f"PID={pid} ({base}) consuming {used_mem} MiB VRAM (threshold: {self.vram_threshold_mb} MiB)"
                    return offending

        except Exception as exc:
            logger.debug("nvidia-smi compute query failed: %s", exc)

        return None

    def detect_colleague_gpu_utilization(self) -> Optional[str]:
        """Detect non-Ollama workload driving high GPU utilization.

        Returns:
            Optional[str]: Description of high GPU utilization, or None if clear.
        """
        if not shutil.which("nvidia-smi"):
            return None

        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return None

            first_line = res.stdout.strip().splitlines()[0].strip()
            util = int(first_line)
            if util < self.gpu_util_threshold:
                return None

            # If LLM inference is currently in flight, utilization is expected
            if self._llm_active:
                return None

            # High utilization detected; verify whether it is solely Ollama
            compute_cmd = [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name",
                "--format=csv,noheader",
            ]
            comp_res = subprocess.run(
                compute_cmd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ollama_active = False

            if comp_res.returncode == 0 and comp_res.stdout.strip():
                for line in comp_res.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 2:
                        continue
                    p_name = parts[1]
                    if self.is_ollama_process(p_name):
                        ollama_active = True
                        break

            # If Ollama is active on the GPU, high utilization is our own workload
            if ollama_active:
                return None

            return (
                f"GPU utilization at {util}% (threshold: {self.gpu_util_threshold}%) "
                f"driven by external workload"
            )

        except Exception as exc:
            logger.debug("nvidia-smi utilization query failed: %s", exc)

        return None

    def detect_high_cpu_usage(self) -> Optional[str]:
        """Detect sustained high host CPU usage via /proc/stat.

        Returns:
            Optional[str]: Description of sustained high CPU usage, or None if normal.
        """
        stat_path = Path("/proc/stat")
        if not stat_path.exists():
            return None

        try:
            line = stat_path.read_text(encoding="utf-8").splitlines()[0]
            fields = line.split()
            if len(fields) < 5 or fields[0] != "cpu":
                return None

            user, nice, system, idle = (
                int(fields[1]),
                int(fields[2]),
                int(fields[3]),
                int(fields[4]),
            )
            iowait = int(fields[5]) if len(fields) > 5 else 0
            irq = int(fields[6]) if len(fields) > 6 else 0
            softirq = int(fields[7]) if len(fields) > 7 else 0

            total = user + nice + system + idle + iowait + irq + softirq
            idle_all = idle + iowait

            if self._prev_cpu_total == 0:
                self._prev_cpu_total = total
                self._prev_cpu_idle = idle_all
                return None

            delta_total = total - self._prev_cpu_total
            delta_idle = idle_all - self._prev_cpu_idle
            self._prev_cpu_total = total
            self._prev_cpu_idle = idle_all

            if delta_total <= 0:
                return None

            cpu_pct = int(((delta_total - delta_idle) * 100) / delta_total)

            if cpu_pct >= self.cpu_threshold_pct:
                self._cpu_high_count += 1
                if self._cpu_high_count >= self.cpu_sustained_polls:
                    return (
                        f"Host CPU load at {cpu_pct}% for {self._cpu_high_count} "
                        f"consecutive checks (threshold: {self.cpu_threshold_pct}%)"
                    )
            else:
                self._cpu_high_count = 0

        except Exception as exc:
            logger.debug("CPU usage detection failed: %s", exc)

        return None

    def detect_competing_workload(self) -> Optional[str]:
        """Perform all workload checks and return the first offending reason found.

        Returns:
            Optional[str]: Description of competing workload, or None if clear.
        """
        gpu_act = self.detect_colleague_gpu_activity()
        if gpu_act:
            return gpu_act

        gpu_util = self.detect_colleague_gpu_utilization()
        if gpu_util:
            return gpu_util

        cpu_act = self.detect_high_cpu_usage()
        if cpu_act:
            return cpu_act

        return None

    def unload_model(self, model_name: Optional[str] = None) -> bool:
        """Unload the active LLM model from VRAM to yield resources immediately.

        Calls the Ollama HTTP API with keep_alive=0, falling back to 'ollama stop'.

        Args:
            model_name: Optional model tag; defaults to config.LLM_MODEL.

        Returns:
            bool: True if model unload succeeded, False otherwise.
        """
        target_model = model_name or getattr(config, "LLM_MODEL", "gemma4-hermes:31b")
        endpoint = getattr(config, "LLM_ENDPOINT", "http://localhost:11434/api/generate")

        # Determine base URL for Ollama API
        base_url = endpoint.rsplit("/", 1)[0]
        unload_url = f"{base_url}/generate"

        logger.info(
            "Yielding GPU: Unloading model '%s' from VRAM via %s...",
            target_model,
            unload_url,
        )

        try:
            resp = requests.post(
                unload_url,
                json={"model": target_model, "keep_alive": 0},
                timeout=5,
            )
            if resp.status_code == 200:
                logger.info("✓ Model '%s' unloaded from VRAM via Ollama API.", target_model)
                return True
        except Exception as exc:
            logger.debug("Ollama API model unload attempt encountered: %s", exc)

        # Fallback to local ollama CLI if available
        if shutil.which("ollama"):
            try:
                stop_res = subprocess.run(
                    ["ollama", "stop", target_model],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if stop_res.returncode == 0:
                    logger.info("✓ Model '%s' unloaded from VRAM via 'ollama stop'.", target_model)
                    return True
            except Exception as cli_exc:
                logger.warning("CLI 'ollama stop' failed: %s", cli_exc)

        logger.warning(
            "Could not explicitly unload model '%s' (Ollama may already be idle).",
            target_model,
        )
        return False

    def trigger_cooldown(
        self,
        reason: str,
        duration_seconds: Optional[int] = None,
        *,
        unload_vram: bool = True,
    ) -> None:
        """Enter a backoff cooldown period and immediately yield resources.

        Args:
            reason: Explanation of why cooldown was triggered.
            duration_seconds: Cooldown duration; defaults to self.cooldown_duration.
            unload_vram: Whether to immediately unload the LLM model from VRAM.
        """
        duration = duration_seconds if duration_seconds is not None else self.cooldown_duration
        self._cooldown_until = time.time() + duration
        self._cooldown_reason = reason
        self._last_offending_workload = reason

        expiry_str = time.strftime(
            "%H:%M:%S", time.localtime(self._cooldown_until)
        )
        logger.warning(
            "⏳ Entering workload cooldown until %s (%ds). Reason: %s",
            expiry_str,
            duration,
            reason,
        )

        if unload_vram:
            try:
                self.unload_model()
            except Exception as exc:
                logger.warning("Model unload during cooldown trigger failed: %s", exc)

    def is_in_cooldown(self) -> bool:
        """Check whether the monitor is currently in a cooldown state.

        Returns:
            bool: True if cooldown is still active, False otherwise.
        """
        return time.time() < self._cooldown_until

    def get_cooldown_remaining(self) -> int:
        """Get the number of remaining seconds in the current cooldown.

        Returns:
            int: Remaining seconds, or 0 if cooldown has expired.
        """
        rem = self._cooldown_until - time.time()
        return max(0, int(rem))

    def is_system_idle(self) -> bool:
        """Check whether the host system GPU and CPU are currently idle.

        Returns:
            bool: True if no competing workloads are detected, False otherwise.
        """
        return self.detect_competing_workload() is None

    def can_run_llm(self) -> Tuple[bool, str]:
        """Check whether an LLM inference request is allowed to execute now.

        Verifies cooldown status and detects active competing workloads. If a
        workload is detected, triggers cooldown and unloads the model automatically.

        Returns:
            Tuple[bool, str]: (allowed, reason_or_status_message).
        """
        if not self.enabled:
            return True, "Workload monitoring is disabled"

        # 1. Check if existing cooldown is active
        if self.is_in_cooldown():
            rem = self.get_cooldown_remaining()
            return False, f"In cooldown ({rem}s remaining): {self._cooldown_reason}"

        # 2. Check for active competing workloads
        offending = self.detect_competing_workload()
        if offending:
            self.trigger_cooldown(reason=f"Workload detected: {offending}")
            return False, f"Yielding to workload: {offending}"

        return True, "System is idle; LLM available"

    def get_status(self) -> Dict[str, Any]:
        """Get structured status dictionary for web UI and diagnostics without side effects.

        Returns:
            Dict[str, Any]: Status summary including GPU, CPU, and cooldown state.
        """
        in_cooldown = self.is_in_cooldown()
        rem = self.get_cooldown_remaining()
        if in_cooldown:
            msg = f"In cooldown ({rem}s remaining): {self._cooldown_reason}"
        elif self._llm_active:
            msg = "LLM inference currently active"
        else:
            msg = "System idle; LLM available"

        return {
            "enabled": self.enabled,
            "llm_active": self._llm_active,
            "can_run_llm": not in_cooldown,
            "status_message": msg,
            "in_cooldown": in_cooldown,
            "cooldown_remaining_seconds": rem,
            "last_cooldown_reason": self._cooldown_reason or None,
            "vram_threshold_mb": self.vram_threshold_mb,
            "gpu_util_threshold": self.gpu_util_threshold,
            "cpu_threshold_pct": self.cpu_threshold_pct,
            "cooldown_duration_seconds": self.cooldown_duration,
        }


# Singleton instance
_monitor_instance: Optional[WorkloadMonitor] = None


def get_workload_monitor() -> WorkloadMonitor:
    """Get or create the singleton WorkloadMonitor instance.

    Returns:
        WorkloadMonitor: Active workload monitor instance.
    """
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = WorkloadMonitor()
    return _monitor_instance
