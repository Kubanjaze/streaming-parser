import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse, os, json, time, warnings
warnings.filterwarnings("ignore")
import pandas as pd
from dotenv import load_dotenv
import anthropic

load_dotenv()
os.environ.setdefault("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))


def pic50_to_class(pic50: float) -> str:
    if pic50 < 5.0:   return "inactive"
    elif pic50 < 6.0: return "weak"
    elif pic50 < 7.0: return "moderate"
    elif pic50 < 8.0: return "potent"
    else:             return "highly_potent"


def build_prompt(row) -> str:
    return (
        f"Analyze this compound and provide a brief SAR comment.\n\n"
        f"Compound: {row['compound_name']}\n"
        f"SMILES: {row['smiles']}\n"
        f"pIC50: {row['pic50']:.2f} ({pic50_to_class(row['pic50'])})\n\n"
        f"Respond in this exact JSON format:\n"
        f'{{"compound": "{row["compound_name"]}", "activity_class": "{pic50_to_class(row["pic50"])}", '
        f'"key_feature": "<one structural feature>", "sar_comment": "<one sentence SAR insight>"}}'
    )


def stream_compound(client, model, prompt, compound_name) -> tuple[str, float, int, int]:
    """Stream response for one compound. Returns (full_text, elapsed_s, input_tok, output_tok)."""
    t0 = time.time()
    full_text = ""
    input_tokens = 0
    output_tokens = 0

    print(f"  Streaming {compound_name}: ", end="", flush=True)
    with client.messages.stream(
        model=model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            full_text += text
            print(".", end="", flush=True)

        # Get usage from final message
        final = stream.get_final_message()
        input_tokens = final.usage.input_tokens
        output_tokens = final.usage.output_tokens

    elapsed = time.time() - t0
    print(f" {elapsed:.2f}s", flush=True)
    return full_text, elapsed, input_tokens, output_tokens


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--input", required=True)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    df = pd.read_csv(args.input).head(args.n)
    client = anthropic.Anthropic()

    print(f"\nPhase 58 — Streaming Structured Output Parser")
    print(f"Model: {args.model} | Compounds: {len(df)}\n")

    records = []
    total_input = 0
    total_output = 0
    total_elapsed = 0
    n_parsed = 0

    for _, row in df.iterrows():
        prompt = build_prompt(row)
        full_text, elapsed, in_tok, out_tok = stream_compound(
            client, args.model, prompt, row["compound_name"]
        )
        total_input += in_tok
        total_output += out_tok
        total_elapsed += elapsed

        # Parse JSON from streamed text
        import re
        json_match = re.search(r'\{.*\}', full_text, re.DOTALL)
        parsed = None
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                n_parsed += 1
            except Exception:
                pass

        records.append({
            "compound_name": row["compound_name"],
            "streamed_text": full_text,
            "parsed": parsed,
            "elapsed_s": round(elapsed, 3),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        })

    # Save outputs
    with open(os.path.join(args.output_dir, "stream_results.json"), "w") as f:
        json.dump(records, f, indent=2)

    cost = (total_input / 1e6 * 0.80) + (total_output / 1e6 * 4.0)
    tokens_per_sec = total_output / total_elapsed if total_elapsed > 0 else 0

    report = (
        f"Phase 58 — Streaming Structured Output Parser\n"
        f"{'='*50}\n"
        f"Model:          {args.model}\n"
        f"Compounds:      {len(df)}\n"
        f"Parse success:  {n_parsed}/{len(df)}\n"
        f"Total time:     {total_elapsed:.2f}s\n"
        f"Avg time/req:   {total_elapsed/len(df):.2f}s\n"
        f"Output tok/s:   {tokens_per_sec:.1f}\n"
        f"Input tokens:   {total_input}\n"
        f"Output tokens:  {total_output}\n"
        f"Est. cost:      ${cost:.4f}\n"
    )
    print(f"\n{report}")

    if records and records[0].get("parsed"):
        print("Sample parsed output:")
        print(json.dumps(records[0]["parsed"], indent=2))

    with open(os.path.join(args.output_dir, "stream_report.txt"), "w") as f:
        f.write(report)
    print(f"Saved: {args.output_dir}/stream_results.json")
    print(f"Saved: {args.output_dir}/stream_report.txt")
    print("\nDone.")


if __name__ == "__main__":
    main()
