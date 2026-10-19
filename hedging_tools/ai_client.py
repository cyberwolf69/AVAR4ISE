"""AgentRouter Responses adapter. Secrets never enter returned diagnostics."""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler
from hedging_tools.analyst import validate_proposal
from hedging_tools.okx_public import OKXPublic

from hedging_tools.paths import PROJECT_ROOT as ROOT


class AIError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AIError("Redirect refused; credential destination must remain fixed")


def settings(path=ROOT / ".env"):
    values = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        raise AIError("Local .env unavailable") from None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise AIError("Invalid .env format")
        values[key.strip()] = value.strip().strip('\"\'')
    base = values.get("AI_BASE_URL", "").rstrip("/")
    profiles = {"https://agentrouter.org/v1": "responses", "https://api.apinex.bond/v1": "chat_completions"}
    if base not in profiles:
        raise AIError("Unexpected API destination; review adapter configuration")
    if values.get("AI_API_STYLE") != profiles[base]:
        raise AIError("Protocol does not match configured router")
    if base == "https://api.apinex.bond/v1":
        values["AI_API_KEY"] = values.get("APINEX_API_KEY", "")
    if not values.get("AI_MODEL") or not values.get("AI_API_KEY"):
        raise AIError("AI_MODEL or AI_API_KEY is empty")
    return values


class AIClient:
    def __init__(self, config=None, opener=None):
        self.config = config if config is not None else settings()
        self.opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=OKXPublic().context)).open

    def request(self, instructions, content, max_tokens=1500):
        base = self.config.get("AI_BASE_URL", "https://agentrouter.org/v1").rstrip("/")
        if base not in ("https://agentrouter.org/v1", "https://api.apinex.bond/v1"):
            raise AIError("Unexpected API destination")
        chat = base == "https://api.apinex.bond/v1"
        body = ({"model": self.config["AI_MODEL"], "messages": [
            {"role": "system", "content": instructions}, {"role": "user", "content": content}],
            "max_tokens": max_tokens, "stream": False} if chat else {
            "model": self.config["AI_MODEL"], "instructions": instructions,
            "input": content, "max_output_tokens": max_tokens, "store": False, "stream": False})
        request = Request(base + ("/chat/completions" if chat else "/responses"), data=json.dumps(body).encode(),
                          headers={"Authorization": "Bearer " + self.config["AI_API_KEY"],
                                   "Content-Type": "application/json", "User-Agent": "AVAR4ISE/0.1"}, method="POST")
        # No automatic retries for billable calls or protocol/model substitutions.
        try:
            with self.opener(request, timeout=45) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise AIError("Response exceeds size limit")
            result = json.loads(raw)
            if chat:
                choices = result.get("choices")
                if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                    raise AIError("Chat response incomplete or ambiguous")
                message = choices[0]["message"]
                if message.get("tool_calls") or message.get("refusal"):
                    raise AIError("Unexpected tool call or refusal")
                texts = [message["content"]]
            else:
                if result.get("status") != "completed":
                    raise AIError("Provider response not completed")
                texts = [part["text"] for item in result.get("output", []) if item.get("type") == "message"
                         for part in item.get("content", []) if part.get("type") == "output_text"]
            if not texts:
                raise AIError("Provider returned no text output")
            text = "".join(texts)
            if self.config["AI_API_KEY"] in text:
                raise AIError("Sensitive content rejected")
            usage = result.get("usage") or {}
            safe_usage = {k: v for k, v in usage.items() if k in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens") and type(v) is int}
            return text, safe_usage
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise AIError(f"AI provider HTTP {code}; response body withheld") from None
        except (URLError, OSError, TimeoutError):
            raise AIError("AI provider network/TLS timeout or connection failure") from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AIError("Invalid provider response format") from None

    def analyze(self, envelope):
        if self.config.get("AI_ENABLED", "false").lower() != "true":
            raise AIError("AI disabled: set AI_ENABLED=true for research calls")
        text, usage = self.request(envelope["instructions"], json.dumps({
            "schema": envelope["response_schema"], "data": envelope["input"],
            "format": "Return only one JSON object. Use the exact packet ID and source IDs."}))
        try:
            proposal = json.loads(text)
            validation = validate_proposal(proposal, envelope)
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
            raise AIError("AI proposal rejected by JSON/schema/evidence/expiry validation") from None
        return {"proposal": proposal, "validation": validation, "usage": usage}


AgentRouter = AIClient  # Backward-compatible alias.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true", required=True,
                        help="Explicit one-shot connection test, even while AI_ENABLED=false")
    parser.parse_args()
    try:
        client = AIClient()
        text, usage = client.request("Reply with exactly OK, with no other text.", "Connection test.", 128)
        if text.strip() != "OK":
            raise AIError("Response received but smoke-test text did not match")
        print(json.dumps({"status": "OK", "protocol": client.config["AI_API_STYLE"], "usage": usage, "execution": "DISABLED"}))
        return 0
    except AIError as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc), "execution": "DISABLED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
