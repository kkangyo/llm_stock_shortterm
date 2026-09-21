"""OpenVINO GenAI용 사전 변환 모델을 Hugging Face에서 받아온다.

optimum-cli로 직접 변환할 필요 없이, 이미 OpenVINO IR(INT4)로 변환되어 올라온
모델을 그대로 받아 config.yaml의 openvino.model_path 경로에 저장한다.

사용법:
    python scripts/download_openvino_model.py                # 기본값: GPU용 7B 모델
    python scripts/download_openvino_model.py --npu           # NPU(Intel AI Boost)용 1.5B 모델
    python scripts/download_openvino_model.py --repo <repo-id> --out <경로>   # 임의 모델
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 이 Windows 콘솔은 레거시 코드페이지(cp1252)라 한글 print()가 UnicodeEncodeError로
# 죽는 경우가 있다 - chcp 65001 없이 실행해도 안전하도록 출력 인코딩을 강제한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# GPU(Arc iGPU 등)에서는 7B급도 무난하게 돌아간다. 기존 ollama.model(qwen2.5:7b)과
# 같은 계열 모델이라 판단 품질을 비교하기도 쉽다.
GPU_REPO = "OpenVINO/Qwen2.5-7B-Instruct-int4-ov"
GPU_OUT = PROJECT_ROOT / "models" / "openvino" / "qwen2.5-7b-instruct-int4-ov"

# Meteor Lake의 1세대 NPU(Intel AI Boost)는 아직 대형 모델을 안정적으로 못 돌리므로
# 검증된 소형 모델을 쓴다.
NPU_REPO = "OpenVINO/Qwen2.5-1.5B-Instruct-int4-ov"
NPU_OUT = PROJECT_ROOT / "models" / "openvino" / "qwen2.5-1.5b-instruct-int4-ov"


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenVINO IR 변환 모델 다운로드")
    parser.add_argument(
        "--npu", action="store_true", help="GPU용 7B 대신 NPU용 1.5B 모델을 받는다"
    )
    parser.add_argument("--repo", default=None, help="임의의 Hugging Face repo id")
    parser.add_argument("--out", default=None, help="저장 경로 (기본: models/openvino/<repo명>)")
    args = parser.parse_args()

    if args.repo:
        repo_id = args.repo
        out_dir = Path(args.out) if args.out else PROJECT_ROOT / "models" / "openvino" / args.repo.split("/")[-1].lower()
    elif args.npu:
        repo_id, out_dir = NPU_REPO, NPU_OUT
    else:
        repo_id, out_dir = GPU_REPO, GPU_OUT

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SystemExit(
            "huggingface_hub가 설치되어 있지 않습니다. "
            "'pip install -r requirements-openvino.txt'를 먼저 실행하세요."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{repo_id} -> {out_dir} 다운로드 중 (수 GB, 네트워크 상태에 따라 시간이 걸릴 수 있음)...")
    snapshot_download(repo_id=repo_id, local_dir=out_dir)
    print(f"완료: {out_dir}")
    print("config.yaml의 openvino.model_path를 위 경로(프로젝트 루트 기준 상대경로)로 맞춰주세요.")
    if args.npu:
        print("NPU를 쓰려면 config.yaml의 openvino.device도 \"NPU\"로 바꿔주세요.")


if __name__ == "__main__":
    main()
