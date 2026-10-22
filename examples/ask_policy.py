"""Read one synthetic policy; explicit local mode is the only model-calling path."""
import argparse
import json

from agentlab.rag import GroundedRAG
from agentlab.local_model import LocalModel
from agentlab.console import configure_utf8_output


def main():
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--tenant", default="campus")
    parser.add_argument("--mode", choices=("extractive", "local"), default="extractive")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=20,
                        help="local model call budget in seconds, (0, 120]")
    parser.add_argument("--max-output-tokens", type=int, default=384,
                        help="local model output budget, 16..2048")
    args = parser.parse_args()
    try:
        if not 0 < args.timeout <= 120 or not 16 <= args.max_output_tokens <= 2048:
            raise ValueError("invalid_model_budget")
        generator = (LocalModel(args.model, timeout=args.timeout, max_output_tokens=args.max_output_tokens)
                     if args.mode == "local" else None)
        result = GroundedRAG(mode=args.mode, model=args.model, generator=generator).answer(
            args.question, tenant=args.tenant)
    except (ValueError, OSError):
        print(json.dumps({"error": "invalid_configuration"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["reason"] == "model_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
