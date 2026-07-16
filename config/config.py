# config.py
import os
import sys
import json
import logging
import random
import re
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("Config")
load_dotenv(override=True)

# =====================================================================
# DYNAMIC CONFIGURATION PARAMETER ENGINE (DSL PARSER & NUMERIC PROXY)
# =====================================================================
class DynamicParameter:
    """
    A robust configuration proxy class that dynamically parses environment settings,
    implements sequence rotations, random choices, ranges, step iterations, type checks,
    and clamping validation, while natively mimicking standard numeric types.
    """
    GLOBAL_STATE = {}

    def __init__(self, env_key: str, default_val, expected_type=float, min_val=None, max_val=None):
        self.env_key = env_key
        self.default_val = default_val
        self.expected_type = expected_type
        self.min_val = min_val
        self.max_val = max_val
        self._override_val = None
        self._seq_index = 0

    def set_override(self, val):
        """Sets a dynamic database or JSON override value."""
        self._override_val = val

    def _get_raw_str(self) -> str:
        if self._override_val is not None:
            return str(self._override_val)
        val = os.getenv(self.env_key)
        if val is not None:
            return str(val).strip()
        return str(self.default_val)

    def evaluate(self):
        """Parses the current raw rule string, evaluates it, type-casts, and clamps boundaries."""
        raw_str = self._get_raw_str()
        try:
            # Bypass parsing for literal strings to prevent syntax errors
            if self.expected_type == str:
                return raw_str

            val = self._parse_and_evaluate_str(raw_str)
            casted = self._cast_value(val)
            
            # Apply strict boundary limits
            if self.min_val is not None and casted < self.min_val:
                logger.warning(f"Bound warning: {self.env_key} evaluated to {casted}, clamped to minimum {self.min_val}")
                casted = self.min_val
            if self.max_val is not None and casted > self.max_val:
                logger.warning(f"Bound warning: {self.env_key} evaluated to {casted}, clamped to maximum {self.max_val}")
                casted = self.max_val
            return casted
        except Exception as e:
            logger.error(f"Failed to evaluate dynamic configuration parameter {self.env_key} from raw '{raw_str}': {str(e)}. Falling back to default.")
            return self._cast_value(self.default_val)

    def _cast_value(self, val):
        if self.expected_type == bool:
            return str(val).lower() in ["true", "1", "yes"]
        if self.expected_type == str:
            return str(val)
        return self.expected_type(val)

    def _parse_and_evaluate_str(self, s: str):
        s = s.strip()
        if not s:
            return self.default_val

        # Pre-execution checks for functional and shorthand notation done FIRST
        if "|" in s and ":" in s:
            parts = [p.strip() for p in s.split("|") if p.strip()]
            population, weights = [], []
            is_valid_weighted = True
            for p in parts:
                if ":" in p:
                    v_str, w_str = p.split(":", 1)
                    try:
                        population.append(v_str.strip())
                        weights.append(float(w_str.strip()))
                    except ValueError:
                        is_valid_weighted = False
                        break
                else:
                    is_valid_weighted = False
                    break
            if is_valid_weighted and population:
                resolved_pop = [float(self._parse_and_evaluate_str(v)) for v in population]
                return random.choices(resolved_pop, weights=weights)[0]

        m_shorthand_step = re.match(r"^([^-]+)-([^:]+):(.+)$", s)
        if m_shorthand_step:
            start, stop, step_part = float(m_shorthand_step.group(1)), float(m_shorthand_step.group(2)), m_shorthand_step.group(3).strip().lower()
            if step_part in ["random", "rand"]:
                return random.uniform(start, stop)
            else:
                step_size = float(step_part)
                steps = self._generate_steps(start, stop, step_size)
                if steps:
                    val = steps[self._seq_index % len(steps)]
                    self._seq_index += 1
                    return val

        if re.match(r"^\s*[0-9]+(?:\.[0-9]+)?\s*-\s*[0-9]+(?:\.[0-9]+)?\s*$", s):
            m_shorthand_range = re.match(r"^([^-]+)-([^-]+)$", s)
            if m_shorthand_range:
                vmin, vmax = float(m_shorthand_range.group(1)), float(m_shorthand_range.group(2))
                return random.uniform(vmin, vmax)

        if "," in s and not ("(" in s or ")" in s):
            parts = [p.strip() for p in s.split(",") if p.strip()]
            if parts:
                val = parts[self._seq_index % len(parts)]
                self._seq_index += 1
                return float(val)

        # Safe token-based lexical analyzer pattern
        token_pattern = re.compile(
            r'\s*(?:([a-zA-Z_][a-zA-Z0-9_]*)\s*\(|([0-9]+(?:\.[0-9]+)?)|([+\-*/%<>=!~]+)|(\()|(\))|([,])|([a-zA-Z_][a-zA-Z0-9_]*)|(\S))\s*'
        )
        tokens = []
        for m in token_pattern.finditer(s):
            func, num, op, lparen, rparen, comma, name, err = m.groups()
            if err:
                raise ValueError(f"Syntax error near token: {err}")
            if func:
                tokens.append(("FUNC", func.lower()))
            elif num:
                tokens.append(("NUM", float(num)))
            elif op:
                tokens.append(("OP", op))
            elif lparen:
                tokens.append(("LPAREN", "("))
            elif rparen:
                tokens.append(("RPAREN", ")"))
            elif comma:
                tokens.append(("COMMA", ","))
            elif name:
                tokens.append(("NAME", name))

        idx = [0]  # List pointer to allow in-recursion index tracking

        def parse_expression():
            # logical comparison operators: expr (OP expr)*
            left = parse_term()
            while idx[0] < len(tokens) and tokens[idx[0]][0] == "OP" and tokens[idx[0]][1] in ["==", "!=", "<", ">", "<=", ">="]:
                op = tokens[idx[0]][1]
                idx[0] += 1
                right = parse_term()
                if op == "==": left = (left == right)
                elif op == "!=": left = (left != right)
                elif op == "<": left = (left < right)
                elif op == ">": left = (left > right)
                elif op == "<=": left = (left <= right)
                elif op == ">=": left = (left >= right)
            return left

        def parse_term():
            # add, subtract, and jitter: term ((+|-|~) term)*
            left = parse_factor()
            while idx[0] < len(tokens) and tokens[idx[0]][0] == "OP" and tokens[idx[0]][1] in ["+", "-", "~"]:
                op = tokens[idx[0]][1]
                idx[0] += 1
                right = parse_factor()
                if op == "+": left = left + right
                elif op == "-": left = left - right
                elif op == "~":
                    left = left + random.uniform(-right, right)
            return left

        def parse_factor():
            # multiply, divide, modulo: factor ((*|/|%) factor)*
            left = parse_primary()
            while idx[0] < len(tokens) and tokens[idx[0]][0] == "OP" and tokens[idx[0]][1] in ["*", "/", "%"]:
                op = tokens[idx[0]][1]
                idx[0] += 1
                right = parse_primary()
                if op == "*": left = left * right
                elif op == "/": left = left / right
                elif op == "%": left = left % right
            return left

        def parse_primary():
            if idx[0] >= len(tokens):
                raise ValueError("Unexpected end of expression")
            t_type, t_val = tokens[idx[0]]
            idx[0] += 1
            if t_type == "NUM":
                return t_val
            elif t_type == "LPAREN":
                expr_val = parse_expression()
                if idx[0] >= len(tokens) or tokens[idx[0]][0] != "RPAREN":
                    raise ValueError("Expected ')' matching '('")
                idx[0] += 1
                return expr_val
            elif t_type == "NAME":
                if t_val.lower() == "true": return True
                if t_val.lower() == "false": return False
                return DynamicParameter.GLOBAL_STATE.get(t_val, 0)
            elif t_type == "FUNC":
                args = []
                if idx[0] < len(tokens) and tokens[idx[0]][0] == "RPAREN":
                    idx[0] += 1
                else:
                    while True:
                        args.append(parse_expression())
                        if idx[0] < len(tokens) and tokens[idx[0]][0] == "COMMA":
                            idx[0] += 1
                        elif idx[0] < len(tokens) and tokens[idx[0]][0] == "RPAREN":
                            idx[0] += 1
                            break
                        else:
                            raise ValueError("Expected ',' or ')' in function parameters")
                return evaluate_function(t_val, args)
            else:
                raise ValueError(f"Unexpected token: {t_val}")

        def evaluate_function(name, args):
            if name == "if":
                if len(args) < 3: raise ValueError("if() requires 3 parameters")
                return args[1] if args[0] else args[2]
            elif name == "set":
                if len(args) < 2: raise ValueError("set() requires 2 parameters: set(var, val)")
                var_name = str(args[0])
                DynamicParameter.GLOBAL_STATE[var_name] = args[1]
                return args[1]
            elif name == "get":
                if len(args) < 1: raise ValueError("get() requires at least 1 parameter")
                var_name = str(args[0])
                default = args[1] if len(args) > 1 else 0
                return DynamicParameter.GLOBAL_STATE.get(var_name, default)
            elif name == "seq":
                if not args: raise ValueError("seq() requires parameters")
                val = args[self._seq_index % len(args)]
                self._seq_index += 1
                return val
            elif name in ["choice", "rand"]:
                if not args: raise ValueError("choice() requires parameters")
                return random.choice(args)
            elif name in ["range", "rand_range"]:
                if len(args) < 2: raise ValueError("range() requires 2 parameters: range(min, max)")
                return random.uniform(args[0], args[1])
            elif name == "step":
                if len(args) < 3: raise ValueError("step() requires 3 parameters: step(start, stop, step_size)")
                steps = self._generate_steps(args[0], args[1], args[2])
                if steps:
                    val = steps[self._seq_index % len(steps)]
                    self._seq_index += 1
                    return val
                return args[0]
            elif name == "rand_step":
                if len(args) < 3: raise ValueError("rand_step() requires 3 parameters")
                steps = self._generate_steps(args[0], args[1], args[2])
                return random.choice(steps) if steps else args[0]
            elif name == "jitter":
                if len(args) < 2: raise ValueError("jitter() requires 2 parameters: jitter(base, amount)")
                return args[0] + random.uniform(-args[1], args[1])
            elif name in ["normal", "gaussian"]:
                if len(args) < 2: raise ValueError("normal() requires 2 parameters: normal(mean, std_dev)")
                return random.gauss(args[0], args[1])
            elif name == "backoff":
                if len(args) < 2: raise ValueError("backoff() requires at least 2 parameters: backoff(base, multiplier, [max_val])")
                base, factor = args[0], args[1]
                max_val = args[2] if len(args) > 2 else None
                val = base * (factor ** self._seq_index)
                self._seq_index += 1
                if max_val is not None:
                    val = min(val, max_val)
                return val
            elif name == "fib":
                if len(args) < 1: raise ValueError("fib() requires at least 1 parameter: fib(base, [max_val])")
                base = args[0]
                max_val = args[1] if len(args) > 1 else None
                def _get_fib(n):
                    if n <= 0: return 0
                    if n == 1: return 1
                    a, b = 0, 1
                    for _ in range(2, n + 1):
                        a, b = b, a + b
                    return b
                val = base * _get_fib(self._seq_index + 1)
                self._seq_index += 1
                if max_val is not None:
                    val = min(val, max_val)
                return val
            elif name == "min": return min(args)
            elif name == "max": return max(args)
            elif name == "sin":
                import math
                return math.sin(args[0])
            elif name == "cos":
                import math
                return math.cos(args[0])
            
            # Raise an evaluation error if the function is not supported by the DSL engine
            raise ValueError(f"Unknown dynamic function: {name}")

        res = parse_expression()
        if idx[0] < len(tokens):
            raise ValueError(f"Dangling tokens at end of expression: {tokens[idx[0]:]}")
        return res

    def _generate_steps(self, start, stop, step):
        if step <= 0:
            return [start]
        res = []
        curr = start
        max_steps = 1000  # Guard constraint to prevent memory exhaustion
        count = 0
        while curr <= stop and count < max_steps:
            res.append(curr)
            curr += step
            count += 1
        return res

    # Numerical proxy operations (magic methods)
    def __float__(self): return float(self.evaluate())
    def __int__(self): return int(self.evaluate())
    def __str__(self): return str(self.evaluate())
    def __repr__(self): return f"{self.evaluate()}"
    def __bool__(self): return bool(self.evaluate())

    # Comparison methods
    def __lt__(self, other): return self.evaluate() < (float(other) if isinstance(other, DynamicParameter) else other)
    def __le__(self, other): return self.evaluate() <= (float(other) if isinstance(other, DynamicParameter) else other)
    def __gt__(self, other): return self.evaluate() > (float(other) if isinstance(other, DynamicParameter) else other)
    def __ge__(self, other): return self.evaluate() >= (float(other) if isinstance(other, DynamicParameter) else other)
    def __eq__(self, other): return self.evaluate() == (float(other) if isinstance(other, DynamicParameter) else other)
    def __ne__(self, other): return self.evaluate() != (float(obj) if hasattr(obj, "to_dict") else other) # Safe comparison

    # Math operations
    def __add__(self, other): return self.evaluate() + (float(other) if isinstance(other, DynamicParameter) else other)
    def __radd__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) + self.evaluate()
    def __sub__(self, other): return self.evaluate() - (float(other) if isinstance(other, DynamicParameter) else other)
    def __rsub__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) - self.evaluate()
    def __mul__(self, other): return self.evaluate() * (float(other) if isinstance(other, DynamicParameter) else other)
    def __rmul__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) * self.evaluate()
    def __truediv__(self, other): return self.evaluate() / (float(other) if isinstance(other, DynamicParameter) else other)
    def __rtruediv__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) / self.evaluate()
    def __floordiv__(self, other): return self.evaluate() // (float(other) if isinstance(other, DynamicParameter) else other)
    def __rfloordiv__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) // self.evaluate()
    def __mod__(self, other): return self.evaluate() % (float(other) if isinstance(other, DynamicParameter) else other)
    def __rmod__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) % self.evaluate()
    def __pow__(self, other): return self.evaluate() ** (float(other) if isinstance(other, DynamicParameter) else other)
    def __rpow__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) ** self.evaluate()

    # Unary operators
    def __pos__(self): return +self.evaluate()
    def __neg__(self): return -self.evaluate()
    def __abs__(self): return abs(self.evaluate())


# =====================================================================
# SYSTEM CENTRAL PARAMETERS REGISTRY & GETATTR ATTRIBUTE DISPATCHER
# =====================================================================
_PARAMS = {
    # Section 4: Pollinations
    "DEFAULT_IMAGE_WIDTH": DynamicParameter("DEFAULT_IMAGE_WIDTH", 1024, int, min_val=1),
    "DEFAULT_IMAGE_HEIGHT": DynamicParameter("DEFAULT_IMAGE_HEIGHT", 1024, int, min_val=1),
    "DEFAULT_VIDEO_DURATION": DynamicParameter("DEFAULT_VIDEO_DURATION", 5, int, min_val=1),
    "POLLINATIONS_SEED_MIN": DynamicParameter("POLLINATIONS_SEED_MIN", 1, int, min_val=1),
    "POLLINATIONS_SEED_MAX": DynamicParameter("POLLINATIONS_SEED_MAX", 999999999, int, min_val=1),
    "POLLINATIONS_UPLOAD_JPEG_QUALITY": DynamicParameter("POLLINATIONS_UPLOAD_JPEG_QUALITY", 95, int, min_val=1, max_val=100),
    "IMAGE_GEN_AUTO_DOWNLOAD": DynamicParameter("IMAGE_GEN_AUTO_DOWNLOAD", True, bool),
    "IMAGE_GEN_AUTO_UPLOAD_TO_GOOGLE": DynamicParameter("IMAGE_GEN_AUTO_UPLOAD_TO_GOOGLE", True, bool),
    "AUDIO_GEN_AUTO_DOWNLOAD": DynamicParameter("AUDIO_GEN_AUTO_DOWNLOAD", True, bool),
    "AUDIO_GEN_AUTO_UPLOAD_TO_GOOGLE": DynamicParameter("AUDIO_GEN_AUTO_UPLOAD_TO_GOOGLE", True, bool),
    "VIDEO_GEN_AUTO_DOWNLOAD": DynamicParameter("VIDEO_GEN_AUTO_DOWNLOAD", True, bool),
    "VIDEO_GEN_AUTO_UPLOAD_TO_GOOGLE": DynamicParameter("VIDEO_GEN_AUTO_UPLOAD_TO_GOOGLE", True, bool),

    # Section 5: Database and Summarization
    "DIALOGS_LIMIT": DynamicParameter("DIALOGS_LIMIT", 50, int, min_val=1),
    "BOOTSTRAP_MESSAGES_LIMIT": DynamicParameter("BOOTSTRAP_MESSAGES_LIMIT", 20, int, min_val=1),
    "MISSED_MESSAGES_LIMIT": DynamicParameter("MISSED_MESSAGES_LIMIT", 50, int, min_val=1),
    "DEBOUNCE_DELAY": DynamicParameter("DEBOUNCE_DELAY", 7.0, float, min_val=0.0),
    "DUPLICATE_CACHE_SIZE": DynamicParameter("DUPLICATE_CACHE_SIZE", 1000, int, min_val=1),
    "MAX_FILE_SIZE": DynamicParameter("MAX_FILE_SIZE", 15 * 1024 * 1024, int, min_val=0),
    "AVATAR_CACHE_TIME": DynamicParameter("AVATAR_CACHE_TIME", 86400, int, min_val=0),
    "MESSAGES_LIMIT": DynamicParameter("MESSAGES_LIMIT", 150, int, min_val=1),
    "CONTEXT_LOCAL_MIN_LIMIT": DynamicParameter("CONTEXT_LOCAL_MIN_LIMIT", 15, int, min_val=1),
    "SUMMARIZATION_MESSAGES_LIMIT": DynamicParameter("SUMMARIZATION_MESSAGES_LIMIT", 500, int, min_val=1),
    "SUMMARIZATION_KEEP_LIMIT": DynamicParameter("SUMMARIZATION_KEEP_LIMIT", 15, int, min_val=1),
    "MAX_TURNS": DynamicParameter("MAX_TURNS", 1000, int, min_val=1),
    "MEDIA_LIMIT": DynamicParameter("MEDIA_LIMIT", 250, int, min_val=1),
    "MEDIA_SEARCH_AUTO_DOWNLOAD": DynamicParameter("MEDIA_SEARCH_AUTO_DOWNLOAD", True, bool),
    "MEDIA_SEARCH_AUTO_UPLOAD_TO_GOOGLE": DynamicParameter("MEDIA_SEARCH_AUTO_UPLOAD_TO_GOOGLE", True, bool),

    # Section 6: Network and Timing Settings
    "TIMERS_LOOP_INTERVAL": DynamicParameter("TIMERS_LOOP_INTERVAL", 1.0, float, min_val=0.1),
    "KEEP_ALIVE_INTERVAL": DynamicParameter("KEEP_ALIVE_INTERVAL", 120, int, min_val=1),
    "CONNECTION_MONITOR_INTERVAL": DynamicParameter("CONNECTION_MONITOR_INTERVAL", 10, int, min_val=1),
    "GEMINI_TIMEOUT": DynamicParameter("GEMINI_TIMEOUT", 90.0, float, min_val=0.1),
    "TYPING_INTERVAL": DynamicParameter("TYPING_INTERVAL", 10.0, float, min_val=0.1),
    "TIMEOUT_SLEEP": DynamicParameter("TIMEOUT_SLEEP", 2.0, float, min_val=0.1),
    "QUEUE_PROMOTION_DELAY": DynamicParameter("QUEUE_PROMOTION_DELAY", 2.0, float, min_val=0.1),
    "RATE_LIMIT_SLEEP": DynamicParameter("RATE_LIMIT_SLEEP", 5.0, float, min_val=0.1),
    "API_ERROR_SLEEP": DynamicParameter("API_ERROR_SLEEP", 2.0, float, min_val=0.1),
    "GEMINI_FREE_RECOVERY_TIME": DynamicParameter("GEMINI_FREE_RECOVERY_TIME", 18000, int, min_val=1),
    "GEMINI_PRO_RECOVERY_TIME": DynamicParameter("GEMINI_PRO_RECOVERY_TIME", 86400, int, min_val=1),
    "GEMINI_DEAD_KEY_COOLDOWN": DynamicParameter("GEMINI_DEAD_KEY_COOLDOWN", 31536000, int, min_val=1),
    "POLLINATIONS_KEY_RECOVERY_TIME": DynamicParameter("POLLINATIONS_KEY_RECOVERY_TIME", 3600, int, min_val=1),
    "KEY_INFO_TIMEOUT": DynamicParameter("KEY_INFO_TIMEOUT", 10.0, float, min_val=0.1),
    "PROFILE_UPDATE_INTERVAL": DynamicParameter("PROFILE_UPDATE_INTERVAL", 3600, int, min_val=1),
    "TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT": DynamicParameter("TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT", 10, int, min_val=1),
    "BOT_RESPONSE_TIMEOUT": DynamicParameter("BOT_RESPONSE_TIMEOUT", 6.0, float, min_val=0.1),
    "BUTTON_CLICK_TIMEOUT": DynamicParameter("BUTTON_CLICK_TIMEOUT", 15.0, float, min_val=0.1),
    "DOWNLOAD_MEDIA_TIMEOUT": DynamicParameter("DOWNLOAD_MEDIA_TIMEOUT", 120.0, float, min_val=0.1),
    "TELEGRAM_ACTION_TIMEOUT": DynamicParameter("TELEGRAM_ACTION_TIMEOUT", 60.0, float, min_val=0.1),
    "CONVERSION_TIMEOUT": DynamicParameter("CONVERSION_TIMEOUT", 30.0, float, min_val=0.1),
    "GENERATE_IMAGE_TIMEOUT": DynamicParameter("GENERATE_IMAGE_TIMEOUT", 180.0, float, min_val=0.1),
    "GENERATE_AUDIO_TIMEOUT": DynamicParameter("GENERATE_AUDIO_TIMEOUT", 120.0, float, min_val=0.1),
    "GENERATE_VIDEO_TIMEOUT": DynamicParameter("GENERATE_VIDEO_TIMEOUT", 180.0, float, min_val=0.1),
    "GOOGLE_UPLOAD_TIMEOUT": DynamicParameter("GOOGLE_UPLOAD_TIMEOUT", 120.0, float, min_val=0.1),
    "PUBLIC_UPLOAD_TIMEOUT": DynamicParameter("PUBLIC_UPLOAD_TIMEOUT", 60.0, float, min_val=0.1),

    # Section 7: Proxy and Anonymization
    "TOR_ROTATION_TIMEOUT": DynamicParameter("TOR_ROTATION_TIMEOUT", 15.0, float, min_val=0.1),
    "POLLINATIONS_MAX_ATTEMPTS": DynamicParameter("POLLINATIONS_MAX_ATTEMPTS", 8, int, min_val=1),
    "TOR_MAX_CONSECUTIVE_FAILURES": DynamicParameter("TOR_MAX_CONSECUTIVE_FAILURES", 2, int, min_val=1),
    "PROXY_CHECK_TIMEOUT": DynamicParameter("PROXY_CHECK_TIMEOUT", 3.0, float, min_val=0.1),

    # Section 8: Sandbox limits and Page Scrapers
    "SQL_SELECT_LIMIT": DynamicParameter("SQL_SELECT_LIMIT", 100, int, min_val=1),
    "SQL_STDOUT_CHAR_LIMIT": DynamicParameter("SQL_STDOUT_CHAR_LIMIT", 3500, int, min_val=1),
    "TELEGRAM_ACTION_CHAR_LIMIT": DynamicParameter("TELEGRAM_ACTION_CHAR_LIMIT", 5000, int, min_val=1),
    "TELEGRAM_ACTION_CONFIRM_LIMIT": DynamicParameter("TELEGRAM_ACTION_CONFIRM_LIMIT", 500, int, min_val=1),
    "VM_STDOUT_NOTICE_LIMIT": DynamicParameter("VM_STDOUT_NOTICE_LIMIT", 1500, int, min_val=1),
    "SANDBOX_COMMAND_CHAR_LIMIT": DynamicParameter("SANDBOX_COMMAND_CHAR_LIMIT", 3000, int, min_val=1),
    "SANDBOX_ALLOWED_FILES": DynamicParameter("SANDBOX_ALLOWED_FILES", "all", str),
    "SANDBOX_BLOCKED_FILES": DynamicParameter("SANDBOX_BLOCKED_FILES", "bot.py,config.py,db_manager.py,key_manager.py,gemini_manager.py,.env,tools.py,sandbox.py,utils.py,downloader.py,registry.py", str),
    "WEB_SEARCH_RESULTS_LIMIT": DynamicParameter("WEB_SEARCH_RESULTS_LIMIT", 50, int, min_val=1),
    "WEB_MEDIA_SEARCH_RESULTS_LIMIT": DynamicParameter("WEB_MEDIA_SEARCH_RESULTS_LIMIT", 3, int, min_val=1, max_val=30),
    "WEB_MEDIA_SEARCH_CANDIDATES_LIMIT": DynamicParameter("WEB_MEDIA_SEARCH_CANDIDATES_LIMIT", 50, int, min_val=1, max_val=100),
    "WEB_DEEP_SEARCH_CANDIDATES_LIMIT": DynamicParameter("WEB_DEEP_SEARCH_CANDIDATES_LIMIT", 3, int, min_val=1, max_val=10),
    "WEB_DEEP_SEARCH_CHAR_LIMIT": DynamicParameter("WEB_DEEP_SEARCH_CHAR_LIMIT", 10000, int, min_val=1000, max_val=50000),
    "SCRAPE_CHAR_LIMIT": DynamicParameter("SCRAPE_CHAR_LIMIT", 4000, int, min_val=1),
    "WEB_SEARCH_TIMEOUT": DynamicParameter("WEB_SEARCH_TIMEOUT", 10.0, float, min_val=0.1),
    "WEB_MEDIA_SEARCH_TIMEOUT": DynamicParameter("WEB_MEDIA_SEARCH_TIMEOUT", 10.0, float, min_val=0.1),
    "SCRAPE_TIMEOUT": DynamicParameter("SCRAPE_TIMEOUT", 10.0, float, min_val=0.1),
    "TELEGRAM_CONNECTION_RETRIES": DynamicParameter("TELEGRAM_CONNECTION_RETRIES", 5, int, min_val=0),
    "TELEGRAM_RETRY_DELAY": DynamicParameter("TELEGRAM_RETRY_DELAY", 5.0, float, min_val=0.1),
    "TELEGRAM_TIMEOUT": DynamicParameter("TELEGRAM_TIMEOUT", 15.0, float, min_val=1.0),

    # Section 10: Advanced Configuration Matrix
    "MESSAGE_POOL_LIMIT": DynamicParameter("MESSAGE_POOL_LIMIT", 50, int, min_val=1),
    "PENDING_QUEUE_LIMIT": DynamicParameter("PENDING_QUEUE_LIMIT", 10, int, min_val=1),
    "TEMP_MEDIA_CLEANUP_INTERVAL": DynamicParameter("TEMP_MEDIA_CLEANUP_INTERVAL", 3600.0, float, min_val=1.0),
    "RECURSIVE_REPLY_DEPTH_LIMIT": DynamicParameter("RECURSIVE_REPLY_DEPTH_LIMIT", 3, int, min_val=1),
    "BOOTSTRAP_DATABASE": DynamicParameter("BOOTSTRAP_DATABASE", True, bool),
    "DB_NAME": DynamicParameter("DB_NAME", "bot_context.db", str),
    "BOT_AVATAR_NAME": DynamicParameter("BOT_AVATAR_NAME", "bot_avatar.jpg", str),
    "SQLITE_JOURNAL_MODE": DynamicParameter("SQLITE_JOURNAL_MODE", "WAL", str),
    "CONTEXT_LOCAL_RATIO": DynamicParameter("CONTEXT_LOCAL_RATIO", 0.7, float),
    "EMOJI_CACHE_DIR_NAME": DynamicParameter("EMOJI_CACHE_DIR_NAME", "emoji_cache", str),
    "AVATAR_CACHE_DIR_NAME": DynamicParameter("AVATAR_CACHE_DIR_NAME", "avatar_cache", str),
    "GIFT_CACHE_DIR_NAME": DynamicParameter("GIFT_CACHE_DIR_NAME", "gift_cache", str),
    "TEMP_MEDIA_DIR_NAME": DynamicParameter("TEMP_MEDIA_DIR_NAME", "temp_media", str),
    "TELEGRAM_AUTO_RECONNECT": DynamicParameter("TELEGRAM_AUTO_RECONNECT", True, bool),
    "READ_ACK_WHITELIST": DynamicParameter("READ_ACK_WHITELIST", "all", str),
    "READ_ACK_BLACKLIST": DynamicParameter("READ_ACK_BLACKLIST", "", str),
    "DEFAULT_IMAGE_NAME": DynamicParameter("DEFAULT_IMAGE_NAME", "generated_image.png", str),
    "DEFAULT_AUDIO_NAME": DynamicParameter("DEFAULT_AUDIO_NAME", "generated_audio.mp3", str),
    "DEFAULT_VIDEO_NAME": DynamicParameter("DEFAULT_VIDEO_NAME", "generated_video.mp4", str),
    "DEFAULT_RESULT_INDEX": DynamicParameter("DEFAULT_RESULT_INDEX", 0, int),
    "SITE_STORAGE_LIMIT_DEFAULT": DynamicParameter("SITE_STORAGE_LIMIT_DEFAULT", 10 * 1024 * 1024, int, min_val=1),
    "SITE_TIMEOUT_DEFAULT": DynamicParameter("SITE_TIMEOUT_DEFAULT", 5.0, float, min_val=0.1),
    "SITE_ALLOWED_IMPORTS_DEFAULT": DynamicParameter("SITE_ALLOWED_IMPORTS_DEFAULT", "json,math,random,urllib,hashlib,datetime", str),
    "SITE_ALLOWED_METHODS_DEFAULT": DynamicParameter("SITE_ALLOWED_METHODS_DEFAULT", "GET,POST,PUT,DELETE,OPTIONS", str),
    "SITE_MAX_REQUEST_SIZE_DEFAULT": DynamicParameter("SITE_MAX_REQUEST_SIZE_DEFAULT", 1048576, int, min_val=1),
    "SITE_STORAGE_LIMIT_MAX": DynamicParameter("SITE_STORAGE_LIMIT_MAX", 52428800, int, min_val=1),
    "SITE_TIMEOUT_MAX": DynamicParameter("SITE_TIMEOUT_MAX", 30.0, float, min_val=0.1),
    "SITE_BLOCKED_IMPORTS_DEFAULT": DynamicParameter("SITE_BLOCKED_IMPORTS_DEFAULT", "os,sys,subprocess,shutil,builtins", str),
    "SITE_BLOCKED_METHODS_DEFAULT": DynamicParameter("SITE_BLOCKED_METHODS_DEFAULT", "", str),
    "SANDBOX_COMMAND_REGEX_BLACKLIST": DynamicParameter("SANDBOX_COMMAND_REGEX_BLACKLIST", r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|tools\.py|sandbox\.py|utils\.py|downloader\.py)", str),
    "SANDBOX_COMMAND_REGEX_WHITELIST": DynamicParameter("SANDBOX_COMMAND_REGEX_WHITELIST", "", str),
    "SITE_COMMAND_WHITELIST": DynamicParameter("SITE_COMMAND_WHITELIST", "all", str),
    "SITE_COMMAND_BLACKLIST": DynamicParameter("SITE_COMMAND_BLACKLIST", "sudo,reboot,shutdown,passwd,chown,chmod", str),
    "SITE_COMMAND_REGEX_BLACKLIST": DynamicParameter("SITE_COMMAND_REGEX_BLACKLIST", r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|tools\.py|sandbox\.py|utils\.py|downloader\.py)", str),
    "SITE_COMMAND_REGEX_WHITELIST": DynamicParameter("SITE_COMMAND_REGEX_WHITELIST", "", str),
    "SITE_PYTHON_WHITELIST": DynamicParameter("SITE_PYTHON_WHITELIST", "all", str),
    "SITE_PYTHON_BLACKLIST": DynamicParameter("SITE_PYTHON_BLACKLIST", "os.system,os.popen,subprocess,shutil.rmtree,eval,exec", str)
}

def __getattr__(name: str):
    """
    Module level attribute getter. Intercepts other module lookups and redirects
    to our internal dynamic configuration parameters, returning raw primitives (int, float, etc.)
    to ensure full compatibility with low-level C-extension libraries.
    """
    if name in _PARAMS:
        return _PARAMS[name].evaluate()
    if name in globals():
        return globals()[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

def __dir__():
    """Allows standard Python autocompletion and module inspection to find all dynamic keys seamlessly."""
    return sorted(list(globals().keys()) + list(_PARAMS.keys()))


# =====================================================================
# SECTION 1: Workspace and System Paths (General Settings)
# =====================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

is_termux = "com.termux" in sys.executable or "/data/data/com.termux" in str(BASE_DIR)
is_emulated = "emulated" in str(BASE_DIR)

if is_termux or is_emulated:
    SAFE_DB_DIR = Path.home() / ".baziliksina"
    SAFE_DB_DIR.mkdir(parents=True, exist_ok=True)
else:
    SAFE_DB_DIR = BASE_DIR

WORKSPACE_DIR = BASE_DIR / "bot_workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

CHARACTER_FILE = os.getenv("CHARACTER_FILE", "character.txt")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# =====================================================================
# SECTION 2: Telegram Core and Session Settings
# =====================================================================
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    raise ValueError("Please specify TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")

try:
    API_ID = int(API_ID)
except ValueError:
    raise ValueError("TELEGRAM_API_ID must be a number")

SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "baziliksina_session")
SESSION_PATH = str(SAFE_DB_DIR / SESSION_NAME)
OWNER_ID = int(os.getenv("OWNER_ID", 2113692455))

TELEGRAM_METHOD_BLACKLIST = {
    "log_out",
    "delete_account",
    "disconnect",
    "sign_in",
    "send_code_request",
    "switch_account",
}

# =====================================================================
# SECTION 3: Core AI Parameters (Gemini Settings)
# =====================================================================
gemini_keys_raw = os.getenv("GEMINI_API_KEYS", "")
GEMINI_KEYS = [k.strip() for k in gemini_keys_raw.split(",") if k.strip()]

if not GEMINI_KEYS:
    raise ValueError("GEMINI_API_KEYS list is empty. Please specify at least one key in .env")

gemini_models_raw = os.getenv("GEMINI_MODELS", "") or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_MODELS = [m.strip() for m in gemini_models_raw.split(",") if m.strip()]

THINKING_LEVEL = os.getenv("THINKING_LEVEL", "high").lower()

TEMPERATURE = os.getenv("TEMPERATURE", None)
if TEMPERATURE:
    try: TEMPERATURE = float(TEMPERATURE)
    except ValueError: TEMPERATURE = None

TOP_P = os.getenv("TOP_P", None)
if TOP_P:
    try: TOP_P = float(TOP_P)
    except ValueError: TOP_P = None

STOP_SEQUENCES = [s.strip() for s in os.getenv("STOP_SEQUENCES", "").split(",") if s.strip()]

OUTPUT_LENGTH = os.getenv("OUTPUT_LENGTH", None)
if OUTPUT_LENGTH:
    try: OUTPUT_LENGTH = int(OUTPUT_LENGTH)
    except ValueError: OUTPUT_LENGTH = None

INPUT_TOKEN_LIMIT = os.getenv("INPUT_TOKEN_LIMIT", None) or os.getenv("MAX_CONTEXT_TOKENS", None)
if INPUT_TOKEN_LIMIT:
    try: INPUT_TOKEN_LIMIT = int(INPUT_TOKEN_LIMIT)
    except ValueError: INPUT_TOKEN_LIMIT = None

SAFETY_HATE_SPEECH = os.getenv("SAFETY_HATE_SPEECH", "BLOCK_NONE")
SAFETY_HARASSMENT = os.getenv("SAFETY_HARASSMENT", "BLOCK_NONE")
SAFETY_SEXUALLY_EXPLICIT = os.getenv("SAFETY_SEXUALLY_EXPLICIT", "BLOCK_NONE")
SAFETY_DANGEROUS_CONTENT = os.getenv("SAFETY_DANGEROUS_CONTENT", "BLOCK_NONE")

# =====================================================================
# SECTION 4: Generative Media Models (Pollinations Settings)
# =====================================================================
pollinations_keys_raw = os.getenv("POLLINATIONS_KEYS", "")
POLLINATIONS_KEYS = [k.strip() for k in pollinations_keys_raw.split(",") if k.strip()]

DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "flux")
MEDIA_RESOLUTION = os.getenv("MEDIA_RESOLUTION", "high").lower()
ASPECT_RATIO = os.getenv("ASPECT_RATIO", "auto").lower()

DEFAULT_AUDIO_VOICE = os.getenv("DEFAULT_AUDIO_VOICE", "nova")
DEFAULT_AUDIO_MODEL = os.getenv("DEFAULT_AUDIO_MODEL", "qwen-tts-instruct")

DEFAULT_VIDEO_MODEL = os.getenv("DEFAULT_VIDEO_MODEL", "wan")
DEFAULT_VIDEO_ASPECT_RATIO = os.getenv("DEFAULT_VIDEO_ASPECT_RATIO", "1:1")

DEFAULT_PUBLIC_UPLOAD_PROVIDER = os.getenv("DEFAULT_PUBLIC_UPLOAD_PROVIDER", "auto")

# =====================================================================
# SECTION 7: Proxy and Anonymization Settings (Tor & Proxy Controls)
# =====================================================================
TOR_HOST = os.getenv("TOR_HOST", "127.0.0.1")
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", 9050))
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", 9051))
TOR_PASSWORD = os.getenv("TOR_PASSWORD", "")
PROXY_STRICT_CHECK = os.getenv("PROXY_STRICT_CHECK", "false").lower() == "true"

def _parse_list(key: str, default: list = None) -> list:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default if default is not None else []
    return [p.strip() for p in raw.split(",") if p.strip()]

def _parse_int_list(key: str, default: list = None) -> list:
    raw = _parse_list(key, default)
    res = []
    for item in raw:
        try:
            res.append(int(item))
        except ValueError:
            res.append(item)
    return res

PROXY_LIST_TELETHON = _parse_list("TELEGRAM_PROXIES")
PROXY_LIST_GEMINI = _parse_list("GEMINI_PROXIES")
PROXY_LIST_POLLINATIONS = _parse_list("POLLINATIONS_PROXIES")
PROXY_LIST_SCRAPER = _parse_list("SCRAPER_PROXIES")

raw_proxy_url = os.getenv("ALL_PROXY") or os.getenv("all_proxy") or ""
if raw_proxy_url:
    if not PROXY_LIST_TELETHON: PROXY_LIST_TELETHON = [raw_proxy_url]
    if not PROXY_LIST_GEMINI: PROXY_LIST_GEMINI = [raw_proxy_url]
    if not PROXY_LIST_POLLINATIONS: PROXY_LIST_POLLINATIONS = [raw_proxy_url]
    if not PROXY_LIST_SCRAPER: PROXY_LIST_SCRAPER = [raw_proxy_url]

def check_proxy_active(proxy_url_str: str) -> bool:
    import socket
    import urllib.parse
    if not proxy_url_str:
        return False
    if not PROXY_STRICT_CHECK:
        return True
    try:
        parsed = urllib.parse.urlparse(proxy_url_str)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return False
        with socket.create_connection((host, port), timeout=float(PROXY_CHECK_TIMEOUT)):
            return True
    except Exception:
        return False

ACTIVE_TELETHON_PROXIES = [p for p in PROXY_LIST_TELETHON if check_proxy_active(p)]
ACTIVE_GEMINI_PROXIES = [p for p in PROXY_LIST_GEMINI if check_proxy_active(p)]
ACTIVE_POLLINATIONS_PROXIES = [p for p in PROXY_LIST_POLLINATIONS if check_proxy_active(p)]
ACTIVE_SCRAPER_PROXIES = [p for p in PROXY_LIST_SCRAPER if check_proxy_active(p)]

is_tor_enabled = check_proxy_active(f"socks5://{TOR_HOST}:{TOR_SOCKS_PORT}")
is_proxy_enabled = len(ACTIVE_TELETHON_PROXIES) > 0 or len(ACTIVE_GEMINI_PROXIES) > 0

if is_proxy_enabled:
    ALL_PROXY = ACTIVE_TELETHON_PROXIES[0] if ACTIVE_TELETHON_PROXIES else (ACTIVE_GEMINI_PROXIES[0] if ACTIVE_GEMINI_PROXIES else raw_proxy_url)
else:
    if "ALL_PROXY" in os.environ: del os.environ["ALL_PROXY"]
    if "all_proxy" in os.environ: del os.environ["all_proxy"]
    ALL_PROXY = None

# =====================================================================
# SECTION 9: AI Generation and Flow Triggers
# =====================================================================
BOOTSTRAP_TRIGGER_GENERATION = os.getenv("BOOTSTRAP_TRIGGER_GENERATION", "true").lower() == "true"
CATCH_UP_TRIGGER_GENERATION = os.getenv("CATCH_UP_TRIGGER_GENERATION", "true").lower() == "true"
USE_SYSTEM_PROMPT = os.getenv("USE_SYSTEM_PROMPT", "true").lower() == "true"

# =====================================================================
# SYSTEM PARAMETERS: ADVANCED MULTI-TIER CONFIGURATION MATRIX
# =====================================================================
AI_RESPONSE_MODE = os.getenv("AI_RESPONSE_MODE", "all").strip().lower()
AI_RESPONSE_TRIGGERS = _parse_list("AI_RESPONSE_TRIGGERS", ["name", "username", "mentioned", "reply_to_me"])

SAVE_INCOMING_MESSAGES = os.getenv("SAVE_INCOMING_MESSAGES", "true").lower() == "true"
SAVE_EDITED_MESSAGES = os.getenv("SAVE_EDITED_MESSAGES", "true").lower() == "true"
SAVE_DELETED_MESSAGES = os.getenv("SAVE_DELETED_MESSAGES", "true").lower() == "true"

SAVE_OUTGOING_NEW_MESSAGES = os.getenv("SAVE_OUTGOING_NEW_MESSAGES", "true").lower() == "true"
SAVE_OUTGOING_EDITED_MESSAGES = os.getenv("SAVE_OUTGOING_EDITED_MESSAGES", "true").lower() == "true"
SAVE_OUTGOING_DELETED_MESSAGES = os.getenv("SAVE_OUTGOING_DELETED_MESSAGES", "true").lower() == "true"

TRIGGER_ON_INCOMING = os.getenv("TRIGGER_ON_INCOMING", "true").lower() == "true"
TRIGGER_ON_EDITED = os.getenv("TRIGGER_ON_EDITED", "false").lower() == "true"
TRIGGER_ON_DELETED = os.getenv("TRIGGER_ON_DELETED", "false").lower() == "true"

TRIGGER_ON_OUTGOING_NEW_MESSAGES = os.getenv("TRIGGER_ON_OUTGOING_NEW_MESSAGES", "false").lower() == "true"
TRIGGER_ON_OUTGOING_EDITED_MESSAGES = os.getenv("TRIGGER_ON_OUTGOING_EDITED_MESSAGES", "false").lower() == "true"
TRIGGER_ON_OUTGOING_DELETED_MESSAGES = os.getenv("TRIGGER_ON_OUTGOING_DELETED_MESSAGES", "false").lower() == "true"

FILTER_POLICY = os.getenv("FILTER_POLICY", "blacklist_first").strip().lower()

# Comma-separated regular expressions / keywords (optional)
MSG_SAVE_WHITELIST = _parse_list("MSG_SAVE_WHITELIST", [])
MSG_SAVE_BLACKLIST = _parse_list("MSG_SAVE_BLACKLIST", [])
MSG_GEN_WHITELIST = _parse_list("MSG_GEN_WHITELIST", [])
MSG_GEN_BLACKLIST = _parse_list("MSG_GEN_BLACKLIST", [])
ALLOWED_MESSAGE_TYPES = _parse_list("ALLOWED_MESSAGE_TYPES", ["text", "voice", "video", "photo", "document", "gif", "sticker", "location", "contact", "poll", "venue", "album", "list"])

SAVE_INCOMING_REACTION_ADD = os.getenv("SAVE_INCOMING_REACTION_ADD", "true").lower() == "true"
SAVE_INCOMING_REACTION_REMOVE = os.getenv("SAVE_INCOMING_REACTION_REMOVE", "true").lower() == "true"
SAVE_OUTGOING_REACTION_ADD = os.getenv("SAVE_OUTGOING_REACTION_ADD", "true").lower() == "true"
SAVE_OUTGOING_REACTION_REMOVE = os.getenv("SAVE_OUTGOING_REACTION_REMOVE", "true").lower() == "true"

TRIGGER_ON_INCOMING_REACTION_ADD = os.getenv("TRIGGER_ON_INCOMING_REACTION_ADD", "false").lower() == "true"
TRIGGER_ON_INCOMING_REACTION_REMOVE = os.getenv("TRIGGER_ON_INCOMING_REACTION_REMOVE", "false").lower() == "true"
TRIGGER_ON_OUTGOING_REACTION_ADD = os.getenv("TRIGGER_ON_OUTGOING_REACTION_ADD", "false").lower() == "true"
TRIGGER_ON_OUTGOING_REACTION_REMOVE = os.getenv("TRIGGER_ON_OUTGOING_REACTION_REMOVE", "false").lower() == "true"

REACTION_WHITELIST = _parse_list("REACTION_WHITELIST", [])
REACTION_BLACKLIST = _parse_list("REACTION_BLACKLIST", [])

SAVE_USER_METADATA = os.getenv("SAVE_USER_METADATA", "true").lower() == "true"
SAVE_CHAT_METADATA = os.getenv("SAVE_CHAT_METADATA", "true").lower() == "true"

# Comma-separated numerical IDs or @usernames
USER_CACHE_WHITELIST = _parse_int_list("USER_CACHE_WHITELIST", [])
USER_CACHE_BLACKLIST = _parse_int_list("USER_CACHE_BLACKLIST", [])
CHAT_CACHE_WHITELIST = _parse_int_list("CHAT_CACHE_WHITELIST", [])
CHAT_CACHE_BLACKLIST = _parse_int_list("CHAT_CACHE_BLACKLIST", [])

CHAT_WHITELIST = _parse_int_list("CHAT_WHITELIST", [])
CHAT_BLACKLIST = _parse_int_list("CHAT_BLACKLIST", [])
READ_ACK_WHITELIST = _parse_list("READ_ACK_WHITELIST", ["all"])
READ_ACK_BLACKLIST = _parse_list("READ_ACK_BLACKLIST", [])

AI_OUTPUT_WHITELIST_REGEX = _parse_list("AI_OUTPUT_WHITELIST_REGEX", [])
AI_OUTPUT_BLACKLIST_REGEX = _parse_list("AI_OUTPUT_BLACKLIST_REGEX", [])

AI_ALLOWED_ROOT_TOOLS = _parse_list("AI_ALLOWED_ROOT_TOOLS", ["all"])
AI_BLOCKED_ROOT_TOOLS = _parse_list("AI_BLOCKED_ROOT_TOOLS", ["execute_python_code", "run_sandboxed_command"])
AI_ALLOWED_CUSTOM_TOOLS = _parse_list("AI_ALLOWED_CUSTOM_TOOLS", ["all"])
AI_BLOCKED_CUSTOM_TOOLS = _parse_list("AI_BLOCKED_CUSTOM_TOOLS", [])

# Custom dynamic tools permissions
CUSTOM_TOOLS_ENABLE = os.getenv("CUSTOM_TOOLS_ENABLE", "true").lower() == "true"
CUSTOM_TOOLS_ALLOW_VIEW = os.getenv("CUSTOM_TOOLS_ALLOW_VIEW", "true").lower() == "true"
CUSTOM_TOOLS_ALLOW_CREATE = os.getenv("CUSTOM_TOOLS_ALLOW_CREATE", "true").lower() == "true"
CUSTOM_TOOLS_ALLOW_EDIT = os.getenv("CUSTOM_TOOLS_ALLOW_EDIT", "true").lower() == "true"
CUSTOM_TOOLS_ALLOW_DELETE = os.getenv("CUSTOM_TOOLS_ALLOW_DELETE", "true").lower() == "true"
CUSTOM_TOOLS_INVOKE_POLICY = os.getenv("CUSTOM_TOOLS_INVOKE_POLICY", "all").strip().lower()

CUSTOM_TOOLS_INVOKE_WHITELIST = _parse_int_list("CUSTOM_TOOLS_INVOKE_WHITELIST", [])
CUSTOM_TOOLS_INVOKE_BLACKLIST = _parse_int_list("CUSTOM_TOOLS_INVOKE_BLACKLIST", [])

CROSS_CHAT_CONTEXT = os.getenv("CROSS_CHAT_CONTEXT", "true").lower() == "true"
STREAMING_GENERATION = os.getenv("STREAMING_GENERATION", "false").lower() == "true"
STREAMING_INTERVAL = float(os.getenv("STREAMING_INTERVAL", 1.5))

RE_SEQ_BLOCK = os.getenv("RE_SEQ_BLOCK", r"<(seq|par|bg)>(.*?)</\1>")
RE_REPLY_TAG = os.getenv("RE_REPLY_TAG", r"(?<!\\)\[Reply(?:\s+to\s+message\s+#?|:\s*)(\d+)\]")
RE_REACT_TAG = os.getenv("RE_REACT_TAG", r"(?<!\\)\[React:\s*(\d+)\s*\|\s*(.*?)\s*\]")
RE_ATTACH_TAG = os.getenv("RE_ATTACH_TAG", r"(?<!\\)\[Attach:\s*([^|\]]+?)\s*(?:\|\s*(.*?))?\s*\]")
RE_EDIT_TAG = os.getenv("RE_EDIT_TAG", r"(?<!\\)\[Edit:\s*(\d+)\s*\|\s*(.*?)\s*\]")
RE_DELETE_TAG = os.getenv("RE_DELETE_TAG", r"(?<!\\)\[Delete:\s*(\d+)\s*\]")
RE_NOOP_TAG = os.getenv("RE_NOOP_TAG", r"(?<!\\)\[(?:NoOp|No_Op_Ignore|NoOpIgnore):\s*([^|\]]+?)\s*(?:\|\s*continue\s*=\s*(true|false))?\s*\]")
RE_TOOL_TAG = os.getenv("RE_TOOL_TAG", r"(?<!\\)\[Tool:\s*([a-zA-Z0-9_]+)\s*\|\s*(.*?)\s*\]")

# =====================================================================
# WEB SERVER SYSTEM PARAMETERS (DYNAMIC MULTI-TIER CONFIG)
# =====================================================================
WEB_SERVER_ENABLE = os.getenv("WEB_SERVER_ENABLE", "true").lower() == "true"
WEB_SERVER_HOST = os.getenv("WEB_SERVER_HOST", "0.0.0.0")
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", 8080))
WEB_SERVER_SUBDOMAIN = os.getenv("WEB_SERVER_SUBDOMAIN", "").strip()
WEB_SERVER_LOG_PATH = os.getenv("WEB_SERVER_LOG_PATH", "bot.log")
WEB_SERVER_IP_ACL = _parse_list("WEB_SERVER_IP_ACL", [])
WEB_SERVER_IP_DETECTION_HOST = os.getenv("WEB_SERVER_IP_DETECTION_HOST", "8.8.8.8")
WEB_SERVER_IP_DETECTION_PORT = int(os.getenv("WEB_SERVER_IP_DETECTION_PORT", 80))
WEB_SERVER_DEFAULT_LOG_LIMIT = int(os.getenv("WEB_SERVER_DEFAULT_LOG_LIMIT", 150))
WEB_SERVER_DEFAULT_META_LIMIT = int(os.getenv("WEB_SERVER_DEFAULT_META_LIMIT", 50))
WEB_SERVER_DEFAULT_TIMER_DELAY = int(os.getenv("WEB_SERVER_DEFAULT_TIMER_DELAY", 60))
WEB_SERVER_REBOOT_DELAY = float(os.getenv("WEB_SERVER_REBOOT_DELAY", 2.0))
PACIFIC_STANDARD_TIME_OFFSET = int(os.getenv("PACIFIC_STANDARD_TIME_OFFSET", -8))
PACIFIC_DAYLIGHT_TIME_OFFSET = int(os.getenv("PACIFIC_DAYLIGHT_TIME_OFFSET", -7))
GEMINI_MIN_COOLDOWN_SECONDS = int(os.getenv("GEMINI_MIN_COOLDOWN_SECONDS", 5))
GEMINI_DAILY_LIMIT_COOLDOWN = int(os.getenv("GEMINI_DAILY_LIMIT_COOLDOWN", 86400))

SANDBOX_CONFIG_WHITELIST = _parse_list("SANDBOX_CONFIG_WHITELIST", ["all"])
SANDBOX_CONFIG_BLACKLIST = _parse_list("SANDBOX_CONFIG_BLACKLIST", ["API_HASH", "TELEGRAM_API_HASH", "GEMINI_API_KEYS", "GEMINI_KEYS", "POLLINATIONS_KEYS", "TOR_PASSWORD", "ALL_PROXY", "all_proxy", "TELEGRAM_PROXIES", "GEMINI_PROXIES", "POLLINATIONS_PROXIES", "SCRAPER_PROXIES"])

GAME_EMOJI_WHITELIST = _parse_list("GAME_EMOJI_WHITELIST", ["🎲", "🎯", "🎳", "🏀", "⚽", "🎰"])
GAME_EMOJI_BLACKLIST = _parse_list("GAME_EMOJI_BLACKLIST", [])

SANDBOX_COMMAND_WHITELIST = _parse_list("SANDBOX_COMMAND_WHITELIST", ["all"])
SANDBOX_COMMAND_BLACKLIST = _parse_list("SANDBOX_COMMAND_BLACKLIST", ["rm", "sudo", "reboot", "shutdown", "init", "passwd", "chown", "chmod", "dd", "mkfs", "parted", "fdisk", "mkswap", "killall", "pkill", "kill", "mv", "systemctl", "service"])

BOT_COMMAND_WHITELIST = _parse_list("BOT_COMMAND_WHITELIST", ["all"])
BOT_COMMAND_BLACKLIST = _parse_list("BOT_COMMAND_BLACKLIST", [])

OUTGOING_FILE_WHITELIST = _parse_list("OUTGOING_FILE_WHITELIST", ["all"])
OUTGOING_FILE_BLACKLIST = _parse_list("OUTGOING_FILE_BLACKLIST", [])
TELEGRAM_ACTION_WHITELIST = _parse_list("TELEGRAM_ACTION_WHITELIST", ["all"])
TELEGRAM_ACTION_BLACKLIST = _parse_list("TELEGRAM_ACTION_BLACKLIST", ["log_out", "delete_account", "disconnect", "sign_in", "send_code_request", "switch_account"])

SANDBOX_PYTHON_WHITELIST = _parse_list("SANDBOX_PYTHON_WHITELIST", ["all"])
SANDBOX_PYTHON_BLACKLIST = _parse_list("SANDBOX_PYTHON_BLACKLIST", ["os.system", "os.popen", "subprocess", "shutil.rmtree", "eval", "exec"])

INCOMING_FILE_WHITELIST = _parse_list("INCOMING_FILE_WHITELIST", ["all"])
INCOMING_FILE_BLACKLIST = _parse_list("INCOMING_FILE_BLACKLIST", [])

INLINE_CALLBACK_WHITELIST = _parse_list("INLINE_CALLBACK_WHITELIST", ["all"])
INLINE_CALLBACK_BLACKLIST = _parse_list("INLINE_CALLBACK_BLACKLIST", [])

KEYBOARD_BUTTON_WHITELIST = _parse_list("KEYBOARD_BUTTON_WHITELIST", ["all"])
KEYBOARD_BUTTON_BLACKLIST = _parse_list("KEYBOARD_BUTTON_BLACKLIST", [])

AI_TAG_WHITELIST = _parse_list("AI_TAG_WHITELIST", ["all"])
AI_TAG_BLACKLIST = _parse_list("AI_TAG_BLACKLIST", [])

AI_BLOCK_WHITELIST = _parse_list("AI_BLOCK_WHITELIST", ["all"])
AI_BLOCK_BLACKLIST = _parse_list("AI_BLOCK_BLACKLIST", [])

CUSTOM_TAG_BLOCK_CODE_WHITELIST = _parse_list("CUSTOM_TAG_BLOCK_CODE_WHITELIST", ["all"])
CUSTOM_TAG_BLOCK_CODE_BLACKLIST = _parse_list("CUSTOM_TAG_BLOCK_CODE_BLACKLIST", [])

GROUP_SETTINGS_WHITELIST = _parse_list("GROUP_SETTINGS_WHITELIST", ["all"])
GROUP_SETTINGS_BLACKLIST = _parse_list("GROUP_SETTINGS_BLACKLIST", [])

CONTACTS_MANAGE_WHITELIST = _parse_list("CONTACTS_MANAGE_WHITELIST", ["all"])
CONTACTS_MANAGE_BLACKLIST = _parse_list("CONTACTS_MANAGE_BLACKLIST", [])

ACCOUNT_SETTINGS_WHITELIST = _parse_list("ACCOUNT_SETTINGS_WHITELIST", ["all"])
ACCOUNT_SETTINGS_BLACKLIST = _parse_list("ACCOUNT_SETTINGS_BLACKLIST", [])

_api_keys_raw = os.getenv("WEB_SERVER_API_KEYS", "")
if _api_keys_raw:
    try:
        WEB_SERVER_API_KEYS = json.loads(_api_keys_raw)
    except Exception:
        # Avoid static backdoors, generate random persistent admin key at startup
        import secrets
        fallback_key = secrets.token_hex(24)
        logger.warning(f"CRITICAL: Failed to parse WEB_SERVER_API_KEYS. Generated secure random fallback token: {fallback_key}")
        WEB_SERVER_API_KEYS = {
            fallback_key: {"permissions": ["all"], "rate_limit": 100}
        }
else:
    # Key dictionary is populated during runtime DB loading to prevent backdoor leaks
    WEB_SERVER_API_KEYS = {}


# =====================================================================
# TIER 3 & 4 RUNTIME LOGIC: Overwrite with config.json and SQLite settings
# =====================================================================
CONFIG_JSON_PATH = BASE_DIR / "config" / "config.json"
if CONFIG_JSON_PATH.exists():
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            local_json = json.load(f)
        for k, v in local_json.items():
            globals()[k] = v
        logger.info("Tier 3 Config Overwrite successfully completed using config.json.")
    except Exception as e:
        logger.error(f"Error loading Tier 3 config.json: {str(e)}")

async def reload_config_from_db(db):
    """Loads dynamic database settings from SQLite and overrides active config parameters in memory."""
    try:
        db_settings = await db.get_all_settings()
        for key, val in db_settings.items():
            try:
                parsed_val = json.loads(val)
            except Exception:
                parsed_val = val
            
            # Check both internal parameters registry and root namespace scope
            if key in _PARAMS:
                _PARAMS[key].set_override(parsed_val)
            elif key in globals() and isinstance(globals()[key], DynamicParameter):
                globals()[key].set_override(parsed_val)
            else:
                globals()[key] = parsed_val
        
        # Security self-healing: if no custom keys exist, generate persistent random secure administrative token
        if not globals().get("WEB_SERVER_API_KEYS"):
            import secrets
            saved_key = await db.get_memory("web_server_persistent_admin_token")
            if not saved_key:
                saved_key = secrets.token_hex(24)
                await db.set_memory("web_server_persistent_admin_token", saved_key)
                logger.info(f"CRITICAL SECURITY: First start, no keys provided. Generated secure administrative token: {saved_key}")
            
            globals()["WEB_SERVER_API_KEYS"] = {
                saved_key: {"permissions": ["all"], "rate_limit": 100}
            }
        logger.info("Tier 4 Config Overwrite successfully synchronized with database settings!")
    except Exception as e:
        logger.error(f"Error reloading config from DB settings table: {str(e)}")

# Dynamic export helper for static analysis tooling
__all__ = sorted(list(globals().keys()) + list(_PARAMS.keys()))
