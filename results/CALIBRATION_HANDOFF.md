# Calibration handoff to Person 3

Person 1 completed the real-model calibration on 9 September 2026 using the
Dockerized `google/flan-t5-small` service with two inference workers and two
PyTorch threads.

## Measured cost model

```text
model_ms = 510.063 + 1.258353 × input_tokens + 26.005798 × max_new_tokens
```

- R²: **0.8912**
- Cheapest modelled grid cell: 731 ms (10 input tokens, 8 output tokens)
- Most expensive modelled grid cell: 6,147 ms (512 input tokens, 192 output tokens)
- Modelled cost ratio: **8.4x**
- Evidence: `results/raw/calibration.json` (48 raw measurements and 16 medians)

Paste-ready values:

```env
COST_C0=510.063
COST_C_IN=1.258353
COST_C_OUT=26.005798
```

The same fit now drives Person 1's mock timing:

```env
MOCK_BASE_MS=510.063
MOCK_MS_PER_IN_TOKEN=1.258353
MOCK_MS_PER_OUT_TOKEN=26.005798
```

## Required Person 3 integration

The calibration feature is the model's effective tokenizer length after its
512-token truncation. Before final security tuning, cap the shield's cheap
input-token approximation at the same 512-token limit. Then retune bucket
capacity and refill values against the new millisecond scale and rerun the
protected traffic profiles. The raw repeat timings show substantial CPU
variance, so the reported R² and limitations must remain visible rather than
being presented as a perfect predictor.
