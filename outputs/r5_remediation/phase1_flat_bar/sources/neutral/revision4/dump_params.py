from revision4.contracts import EffectiveConfig

config = EffectiveConfig()
all_params = {attr: getattr(config, attr) for attr in dir(config) if not attr.startswith("_") and not callable(getattr(config, attr))}

print(f"Total tuneable parameters discovered: {len(all_params)}")
for k, v in sorted(all_params.items()):
    print(f"  • {k}: {v} ({type(v).__name__})")
