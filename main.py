from data_alchemy import build_demo, load_models


def main() -> None:
    models = load_models()
    demo = build_demo(models)
    demo.launch()


if __name__ == "__main__":
    main()
