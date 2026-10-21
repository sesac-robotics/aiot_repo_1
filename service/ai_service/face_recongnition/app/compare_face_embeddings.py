#!/usr/bin/env python3
"""Compare one registered-face JSON with one PC2 capture JSON (stdlib only)."""
import argparse
import json
import math
from pathlib import Path


def load_json(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f'{path}: JSON object expected')
    return data


def validated_vector(data, path):
    vec = data.get('embedding')
    if not isinstance(vec, list) or len(vec) != 512:
        raise ValueError(f'{path}: embedding must contain exactly 512 numbers')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vec):
        raise ValueError(f'{path}: embedding has non-finite or non-numeric entries')
    magnitude = math.sqrt(sum(float(x) ** 2 for x in vec))
    if magnitude == 0 or not math.isfinite(magnitude):
        raise ValueError(f'{path}: invalid zero or non-finite embedding')
    return [float(x) / magnitude for x in vec]


def main():
    parser = argparse.ArgumentParser(description='Compare registered and captured face embeddings')
    parser.add_argument('--registered', required=True, help='db_embeddings/patient_001.json')
    parser.add_argument('--captured', required=True, help='output/<event_id>.json')
    parser.add_argument('--threshold', type=float, default=0.5, help='Provisional cosine similarity threshold')
    args = parser.parse_args()
    if not math.isfinite(args.threshold) or not -1 <= args.threshold <= 1:
        parser.error('threshold must be between -1 and 1')

    registered = load_json(args.registered)
    captured = load_json(args.captured)
    if captured.get('status') != 'OK':
        raise ValueError(f"Captured face extraction failed: {captured.get('status')}")
    if registered.get('model') != captured.get('model'):
        raise ValueError('Registered and captured embeddings use different models')
    if not isinstance(registered.get('patient_id'), str) or not registered['patient_id'].strip():
        raise ValueError('Registered JSON has no valid patient_id')
    if registered.get('embedding_dim') != 512 or captured.get('embedding_dim') != 512:
        raise ValueError('Both embedding_dim fields must be 512')

    a = validated_vector(registered, args.registered)
    b = validated_vector(captured, args.captured)
    similarity = sum(x * y for x, y in zip(a, b))
    matched = similarity >= args.threshold
    result = {
        'event_id': captured.get('event_id'),
        'patient_id': registered['patient_id'] if matched else 'Unknown',
        'candidate_id': registered['patient_id'],
        'similarity': round(similarity, 6),
        'threshold': args.threshold,
        'matched': matched,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
