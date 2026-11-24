"""Toy training job: fit y = 3x + 1 + noise by gradient descent. Numpy only, so it runs on any node; the point is
the path (sync, submit, log, run-end sorting), not the model.

    python train.py --steps 500 --lr 0.1 --seed 0 --out <dir>
"""
import argparse, json, os

import numpy as np


def train(steps, lr, seed):
    rng = np.random.default_rng(seed)
    x = rng.uniform(-1, 1, 1024)
    y = 3 * x + 1 + 0.1 * rng.normal(size=x.size)
    w = b = 0.0
    for step in range(1, steps + 1):
        err = w * x + b - y
        w -= lr * 2 * np.mean(err * x)
        b -= lr * 2 * np.mean(err)
        if step % max(1, steps // 5) == 0:
            print("step %d loss %.5f" % (step, np.mean(err ** 2)), flush=True)
    return {"w": w, "b": b, "loss": float(np.mean((w * x + b - y) ** 2))}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=500)
    ap.add_argument("--lr", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="runs/toy")
    a = ap.parse_args()
    res = train(a.steps, a.lr, a.seed)
    assert abs(res["w"] - 3) < 0.1 and abs(res["b"] - 1) < 0.1, res   # the fit found the line
    os.makedirs(a.out, exist_ok=True)
    json.dump(res, open(os.path.join(a.out, "metrics.json"), "w"))
    print("RUN_DONE", json.dumps(res))
