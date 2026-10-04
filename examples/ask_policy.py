"""Read one synthetic policy; explicit local mode is the only model-calling path."""
import argparse
import json

from agentlab.rag import GroundedRAG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--tenant", default="campus")
    parser.add_argument("--mode", choices=("extractive", "local"), default="extractive")
    parser.add_argument("--model")
    args = parser.parse_args()
    try:
        result = GroundedRAG(mode=args.mode, model=args.model).answer(args.question, tenant=args.tenant)
    except (ValueError, OSError):
        print(json.dumps({"error": "invalid_configuration"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["reason"] == "model_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
