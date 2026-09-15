import os

source_file = 'institutional_engine_v34_B1_resilience.py'
target_file = 'institutional_engine_v34_B12_resilience.py'

old_block = '''    @staticmethod
    def _is_transient_observation_exception(exc: Exception) -> bool:
        \"\"\"Only classify known transport failures as transient; unknown errors fail closed.\"\"\"
        cls = type(exc)
        if cls.__name__ in {"TimeoutError", "ConnectionError", "ReadTimeout", "ConnectTimeout"}:
            return True
        module = getattr(cls, "__module__", "")
        return module.startswith("requests.exceptions") and cls.__name__ in {
            "Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError"
        }'''

new_block = '''    @staticmethod
    def _is_transient_observation_exception(exc: Exception) -> bool:
        \"\"\"Only classify known transport failures as transient; unknown errors fail closed.\"\"\"
        cls = type(exc)
        
        if cls.__name__ in {"TimeoutError", "ConnectionError", "ReadTimeout", "ConnectTimeout"}:
            return True
            
        module = getattr(cls, "__module__", "")
        if module.startswith("requests.exceptions") and cls.__name__ in {
            "Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError"
        }:
            return True
            
        # Kite Connect network failures (Decoupled from direct import)
        if module.startswith("kiteconnect.exceptions") and cls.__name__ == "NetworkException":
            code = getattr(exc, "code", None)
            
            if code in {502, 503, 504}:
                return True
                
            return False
            
        return False'''

with open(source_file, 'r', encoding='utf-8') as f:
    content = f.read()

if old_block in content:
    content = content.replace(old_block, new_block)
    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Successfully generated {target_file}")
else:
    print("CRITICAL: Could not find exact B1 block to replace. Aborting.")
