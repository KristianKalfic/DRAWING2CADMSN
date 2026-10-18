"""Das Backend einer Agentensitzung: welches Modell über welchen Endpunkt antwortet, und die Umgebung der CLI dafür.

Jedes Backend spricht die Anthropic Messages API und braucht keinen eigenen Codepfad: die Claude-Anmeldung oder ein
Anthropic-Schlüssel (keine base_url), und OpenRouter oder Ollama (base_url, siehe backend_env).

Das Modul braucht nur die Standardbibliothek, damit die Web-Oberfläche es ohne das SDK einbinden kann.
"""

import os
import shlex
import sys
from dataclasses import dataclass, replace

from d2c.config import llm_settings

# Damit kein Request einen Anthropic-Modellnamen enthält: die Aliasse und die Hintergrund- und Subagentenmodelle.
MODEL_VARS = (
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
SESSION_ENV = {
    "CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH": "16000",  # die CLI schneidet Serveranweisungen bei 4096 Zeichen ab
    # Alle Werkzeuge im ersten Request. Mit Tool-Suche (Standard an der Anthropic-API) müsste das Modell die Werkzeuge
    # erst suchen, und ohne eingebaute Werkzeuge hat es nichts, womit es suchen könnte.
    "ENABLE_TOOL_SEARCH": "false",
    # Start des MCP-Servers: Nach einer frischen Installation prüft macOS jede neue Bibliothek beim ersten Laden; der
    # erste Import von OCP und CadQuery dauerte so über zwei Minuten (gemessen 140 s), danach wenige Sekunden. Das
    # Standardlimit der CLI (30 s) beendete den ersten Lauf mit "MCP-Server ist failed".
    "MCP_TIMEOUT": "600000",
}
SECRETS = ("D2C_LLM_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
LOCAL_CONTEXT = "64000"  # Standard von D2C_LLM_CONTEXT: so läuft der lokale Ollama (OLLAMA_CONTEXT_LENGTH=64000)
LOCAL_TIMEOUT_MS = "1800000"  # Zeitlimit je Anfrage: ein Zug eines 27B-Modells dauert bis zu mehreren Minuten


@dataclass(frozen=True)
class Backend:
    """Modell, Endpunkt und Schlüssel einer Sitzung; `lite` wählt die kleinen Werkzeugsätze (None: je nach Modell)."""

    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    lite: bool | None = None

    def check(self) -> None:
        """Eine base_url braucht ein Modell, sonst fragt die CLI den Endpunkt nach einem Anthropic-Modellnamen; sonst ValueError."""
        if self.base_url and not self.model:
            raise ValueError(
                "A base_url requires a model (choose a model or set D2C_LLM_MODEL): "
                "otherwise the CLI would ask the endpoint for an Anthropic model name"
            )

    def is_local(self) -> bool:
        """Ob die Sitzung ein nicht-Claude-Modell über einen eigenen Endpunkt anspricht."""
        return bool(self.base_url and self.model and not self.model.startswith("claude"))

    def use_lite(self) -> bool:
        """Ob die Sitzung die kleinen Werkzeugsätze nutzt: ausdrücklich gewählt, sonst für ein lokales Modell."""
        return self.is_local() if self.lite is None else self.lite

    def for_build(self, model: str | None, base_url: str | None) -> "Backend":
        """Das Backend der Bausitzung: `model` und `base_url` ersetzen die eigenen, wo sie gesetzt sind."""
        return replace(self, model=model or self.model, base_url=base_url or self.base_url)


def backend_env(backend: Backend) -> dict[str, str]:
    """Die Umgebungsvariablen, die die CLI auf ihr Backend zeigen.

    Ohne base_url nutzt die CLI die Claude-Anmeldung (nichts zu setzen) oder einen Anthropic-Schlüssel. Mit base_url
    spricht sie einen Anthropic-kompatiblen Endpunkt an (OpenRouter, Ollama, ein Gateway) mit Bearer-Token; der
    Anthropic-Schlüssel muss dabei ein leerer String sein, nicht unset, sonst fällt die CLI auf eine anthropische
    Anmeldung zurück.
    """
    if not backend.base_url:
        return {"ANTHROPIC_API_KEY": backend.api_key} if backend.api_key else {}
    env = {
        "ANTHROPIC_BASE_URL": backend.base_url,
        "ANTHROPIC_AUTH_TOKEN": backend.api_key or "ollama",
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if backend.model:
        env |= dict.fromkeys(MODEL_VARS, backend.model)
        if not backend.model.startswith("claude"):
            # die CLI nimmt für eine unbekannte Modellkennung 200k an; hier die Fenstergröße des lokalen Servers
            llm = llm_settings()
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = llm["context"] or LOCAL_CONTEXT
            env["API_TIMEOUT_MS"] = LOCAL_TIMEOUT_MS
            if llm["thinking"]:
                env["MAX_THINKING_TOKENS"] = llm["thinking"]
    return env


def server_config(server: str) -> dict:
    """Der MCP-Server, wie die CLI ihn erreicht.

    "stdio" startet diesen Interpreter mit `-m d2c.server`, eine http(s)-URL verbindet zu einem laufenden Server, jeder
    andere Wert ist eine Kommandozeile (z. B. `docker run -i --rm --network none -v ...:/data:ro d2c`). Die CLI reicht
    ihre eigene Umgebung an einen stdio-Server weiter, daher werden die D2C_-Einstellungen explizit übergeben, ohne
    D2C_LLM_*, und jede Zugangsnummer wird geleert.
    """
    if server.startswith(("http://", "https://")):
        return {"type": "http", "url": server}
    env = {k: v for k, v in os.environ.items() if k.startswith("D2C_") and not k.startswith("D2C_LLM_")}
    command = [sys.executable, "-m", "d2c.server"] if server in ("stdio", "") else shlex.split(server)
    return {"type": "stdio", "command": command[0], "args": command[1:], "env": env | dict.fromkeys(SECRETS, "")}
