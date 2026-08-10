# config.py
import os
import sys
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, List, Dict, Optional
from dotenv import load_dotenv

logger = logging.getLogger("Config")
load_dotenv(override=True)

# =====================================================================
# ENHANCED DYNAMIC CONFIGURATION PARAMETER ENGINE (DSL PARSER & PROXY)
# =====================================================================
class DynamicParameter:
    """
    An enhanced configuration proxy class that dynamically parses environment settings,
    supports sequence rotations, random choices, ranges, step iterations, type casts,
    min/max clamping, optional DSL feature toggling, and DSL function white/blacklists.
    """
    GLOBAL_STATE = {}

    def __init__(
        self, 
        env_key: str, 
        default_val: Any, 
        expected_type: type = float, 
        min_val: Optional[float] = None, 
        max_val: Optional[float] = None,
        allow_dsl: bool = True,
        dsl_whitelist: Optional[List[str]] = None,
        dsl_blacklist: Optional[List[str]] = None,
        description: str = ""
    ):
        self.env_key = env_key
        self.default_val = default_val
        self.expected_type = expected_type
        self.min_val = min_val
        self.max_val = max_val
        self.allow_dsl = allow_dsl
        self.dsl_whitelist = [w.lower().strip() for w in dsl_whitelist] if dsl_whitelist else []
        self.dsl_blacklist = [b.lower().strip() for b in dsl_blacklist] if dsl_blacklist else []
        self.description = description
        self._override_val = None
        self._seq_index = 0

    def set_override(self, val: Any):
        """Sets a dynamic database or JSON override value."""
        self._override_val = val

    def _get_raw_str(self) -> str:
        if self._override_val is not None:
            return str(self._override_val)
        val = os.getenv(self.env_key)
        if val is not None:
            return str(val).strip()
        return str(self.default_val)

    def evaluate(self) -> Any:
        """Parses the current raw rule string, evaluates DSL expressions, type-casts, and clamps boundaries."""
        raw_str = self._get_raw_str()
        try:
            if self.expected_type in [str, list, dict] and not self.allow_dsl:
                return self._cast_value(raw_str)

            if not self.allow_dsl:
                return self._cast_value(raw_str)

            val = self._parse_and_evaluate_str(raw_str)
            casted = self._cast_value(val)
            
            if isinstance(casted, (int, float)):
                if self.min_val is not None and casted < self.min_val:
                    logger.warning(f"Bound warning: {self.env_key} evaluated to {casted}, clamped to min {self.min_val}")
                    casted = self.expected_type(self.min_val)
                if self.max_val is not None and casted > self.max_val:
                    logger.warning(f"Bound warning: {self.env_key} evaluated to {casted}, clamped to max {self.max_val}")
                    casted = self.expected_type(self.max_val)
            return casted
        except Exception as e:
            logger.error(f"Failed to evaluate parameter {self.env_key} from raw '{raw_str}': {str(e)}. Falling back to default.")
            return self._cast_value(self.default_val)

    def _cast_value(self, val: Any) -> Any:
        if self.expected_type == bool:
            return str(val).lower() in ["true", "1", "yes"]
        if self.expected_type == str:
            return str(val)
        if self.expected_type == list:
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                s = val.strip()
                if not s:
                    return []
                if s.startswith("[") and s.endswith("]"):
                    try: return json.loads(s)
                    except Exception: pass
                return [p.strip() for p in s.split(",") if p.strip()]
            return [val]
        if self.expected_type == dict:
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try: return json.loads(val)
                except Exception: return self.default_val
            return self.default_val
        return self.expected_type(val)

    def _is_dsl_func_allowed(self, func_name: str) -> bool:
        fn = func_name.lower().strip()
        if self.dsl_blacklist and fn in self.dsl_blacklist:
            return False
        if self.dsl_whitelist and fn not in self.dsl_whitelist and "all" not in self.dsl_whitelist:
            return False
        return True

    def _parse_and_evaluate_str(self, s: str) -> Any:
        s = s.strip()
        if not s:
            return self.default_val

        # Pre-checks for weighted choices: "val1:weight1 | val2:weight2"
        if "|" in s and ":" in s and self._is_dsl_func_allowed("choice"):
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
                resolved_pop = [self._parse_and_evaluate_str(v) for v in population]
                return random.choices(resolved_pop, weights=weights)[0]

        # Shorthand step notation: start-stop:step
        m_shorthand_step = re.match(r"^([^-]+)-([^:]+):(.+)$", s)
        if m_shorthand_step and self._is_dsl_func_allowed("step"):
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

        # Shorthand range notation: vmin-vmax
        if re.match(r"^\s*[0-9]+(?:\.[0-9]+)?\s*-\s*[0-9]+(?:\.[0-9]+)?\s*$", s) and self._is_dsl_func_allowed("range"):
            m_shorthand_range = re.match(r"^([^-]+)-([^-]+)$", s)
            if m_shorthand_range:
                vmin, vmax = float(m_shorthand_range.group(1)), float(m_shorthand_range.group(2))
                return random.uniform(vmin, vmax)

        # Comma rotation
        if "," in s and not ("(" in s or ")" in s) and self._is_dsl_func_allowed("seq"):
            parts = [p.strip() for p in s.split(",") if p.strip()]
            if parts:
                val = parts[self._seq_index % len(parts)]
                self._seq_index += 1
                try: return float(val)
                except ValueError: return val

        # Token-based lexical analyzer
        token_pattern = re.compile(
            r'\s*(?:([a-zA-Z_][a-zA-Z0-9_]*)\s*\(|([0-9]+(?:\.[0-9]+)?)|([+\-*/%<>=!~]+)|(\()|(\))|([,])|([a-zA-Z_][a-zA-Z0-9_]*)|(\S))\s*'
        )
        tokens = []
        for m in token_pattern.finditer(s):
            func, num, op, lparen, rparen, comma, name, err = m.groups()
            if err: raise ValueError(f"Syntax error near token: {err}")
            if func: tokens.append(("FUNC", func.lower()))
            elif num: tokens.append(("NUM", float(num)))
            elif op: tokens.append(("OP", op))
            elif lparen: tokens.append(("LPAREN", "("))
            elif rparen: tokens.append(("RPAREN", ")"))
            elif comma: tokens.append(("COMMA", ","))
            elif name: tokens.append(("NAME", name))

        idx = [0]

        def parse_expression():
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
            left = parse_factor()
            while idx[0] < len(tokens) and tokens[idx[0]][0] == "OP" and tokens[idx[0]][1] in ["+", "-", "~"]:
                op = tokens[idx[0]][1]
                idx[0] += 1
                right = parse_factor()
                if op == "+": left = left + right
                elif op == "-": left = left - right
                elif op == "~": left = left + random.uniform(-right, right)
            return left

        def parse_factor():
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
            if idx[0] >= len(tokens): raise ValueError("Unexpected end of expression")
            t_type, t_val = tokens[idx[0]]
            
            # Support unary minus (-) and unary plus (+) for negative numbers
            if t_type == "OP" and t_val == "-":
                idx[0] += 1
                return -parse_primary()
            if t_type == "OP" and t_val == "+":
                idx[0] += 1
                return parse_primary()

            idx[0] += 1
            if t_type == "NUM": return t_val
            elif t_type == "LPAREN":
                expr_val = parse_expression()
                if idx[0] >= len(tokens) or tokens[idx[0]][0] != "RPAREN": raise ValueError("Expected ')' matching '('")
                idx[0] += 1
                return expr_val
            elif t_type == "NAME":
                if t_val.lower() == "true": return True
                if t_val.lower() == "false": return False
                return DynamicParameter.GLOBAL_STATE.get(t_val, 0)
            elif t_type == "FUNC":
                if not self._is_dsl_func_allowed(t_val):
                    raise ValueError(f"DSL function '{t_val}' is blocked for parameter {self.env_key}")
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
            if name == "if": return args[1] if args[0] else args[2]
            elif name == "set":
                var_name = str(args[0])
                DynamicParameter.GLOBAL_STATE[var_name] = args[1]
                return args[1]
            elif name == "get":
                var_name = str(args[0])
                default = args[1] if len(args) > 1 else 0
                return DynamicParameter.GLOBAL_STATE.get(var_name, default)
            elif name == "seq":
                val = args[self._seq_index % len(args)]
                self._seq_index += 1
                return val
            elif name in ["choice", "rand"]: return random.choice(args)
            elif name in ["range", "rand_range"]: return random.uniform(args[0], args[1])
            elif name == "step":
                steps = self._generate_steps(args[0], args[1], args[2])
                if steps:
                    val = steps[self._seq_index % len(steps)]
                    self._seq_index += 1
                    return val
                return args[0]
            elif name == "rand_step":
                steps = self._generate_steps(args[0], args[1], args[2])
                return random.choice(steps) if steps else args[0]
            elif name == "jitter": return args[0] + random.uniform(-args[1], args[1])
            elif name in ["normal", "gaussian"]: return random.gauss(args[0], args[1])
            elif name == "backoff":
                base, factor = args[0], args[1]
                max_val = args[2] if len(args) > 2 else None
                val = base * (factor ** self._seq_index)
                self._seq_index += 1
                if max_val is not None: val = min(val, max_val)
                return val
            elif name == "fib":
                base = args[0]
                max_val = args[1] if len(args) > 1 else None
                def _get_fib(n):
                    if n <= 0: return 0
                    if n == 1: return 1
                    a, b = 0, 1
                    for _ in range(2, n + 1): a, b = b, a + b
                    return b
                val = base * _get_fib(self._seq_index + 1)
                self._seq_index += 1
                if max_val is not None: val = min(val, max_val)
                return val
            elif name == "min": return min(args)
            elif name == "max": return max(args)
            elif name == "sin":
                import math
                return math.sin(args[0])
            elif name == "cos":
                import math
                return math.cos(args[0])
            raise ValueError(f"Unknown dynamic function: {name}")

        res = parse_expression()
        if idx[0] < len(tokens):
            raise ValueError(f"Dangling tokens at end of expression: {tokens[idx[0]:]}")
        return res

    def _generate_steps(self, start, stop, step):
        if step <= 0: return [start]
        res = []
        curr = start
        count = 0
        while curr <= stop and count < 1000:
            res.append(curr)
            curr += step
            count += 1
        return res

    # Numerical & Logical proxy magic methods
    def __float__(self): return float(self.evaluate())
    def __int__(self): return int(self.evaluate())
    def __str__(self): return str(self.evaluate())
    def __repr__(self): return f"{self.evaluate()}"
    def __bool__(self): return bool(self.evaluate())
    def __lt__(self, other): return self.evaluate() < (float(other) if isinstance(other, DynamicParameter) else other)
    def __le__(self, other): return self.evaluate() <= (float(other) if isinstance(other, DynamicParameter) else other)
    def __gt__(self, other): return self.evaluate() > (float(other) if isinstance(other, DynamicParameter) else other)
    def __ge__(self, other): return self.evaluate() >= (float(other) if isinstance(other, DynamicParameter) else other)
    def __eq__(self, other): return self.evaluate() == (float(other) if isinstance(other, DynamicParameter) else other)
    def __ne__(self, other): return self.evaluate() != (float(other) if isinstance(other, DynamicParameter) else other)
    def __add__(self, other): return self.evaluate() + (float(other) if isinstance(other, DynamicParameter) else other)
    def __radd__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) + self.evaluate()
    def __sub__(self, other): return self.evaluate() - (float(other) if isinstance(other, DynamicParameter) else other)
    def __rsub__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) - self.evaluate()
    def __mul__(self, other): return self.evaluate() * (float(other) if isinstance(other, DynamicParameter) else other)
    def __rmul__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) * self.evaluate()
    def __truediv__(self, other): return self.evaluate() / (float(other) if isinstance(other, DynamicParameter) else other)
    def __rtruediv__(self, other): return (float(other) if isinstance(other, DynamicParameter) else other) / self.evaluate()


# =====================================================================
# ALL DYNAMIC PARAMETERS REGISTRY (_PARAMS DICTIONARY)
# =====================================================================
_PARAMS: Dict[str, DynamicParameter] = {
    # --- 1. Telegram Core, Sessions & Admin Ranks ---
    "API_ID": DynamicParameter("TELEGRAM_API_ID", 0, int, allow_dsl=False, description="Telegram API ID alias"),
    "API_HASH": DynamicParameter("TELEGRAM_API_HASH", "", str, allow_dsl=False, description="Telegram API Hash alias"),
    "SESSION_NAME": DynamicParameter("TELEGRAM_SESSION_NAME", "baziliksina_session", str, allow_dsl=False, description="Session name alias"),
    "TELEGRAM_API_ID": DynamicParameter("TELEGRAM_API_ID", 0, int, allow_dsl=False, description="Telegram API ID"),
    "TELEGRAM_API_HASH": DynamicParameter("TELEGRAM_API_HASH", "", str, allow_dsl=False, description="Telegram API Hash"),
    "TELEGRAM_SESSION_NAME": DynamicParameter("TELEGRAM_SESSION_NAME", "baziliksina_session", str, allow_dsl=False, description="Telegram session name"),
    "OWNER_ID": DynamicParameter("OWNER_ID", 2113692455, int, allow_dsl=False, description="Sole Creator numerical Telegram ID"),
    "ADMINS": DynamicParameter("ADMINS", {2113692455: {"rank": 100, "permissions": ["all"]}}, dict, allow_dsl=False, description="Immutable root admins mapping"),

    # --- 2. Core AI & Gemini Settings ---
    "GEMINI_API_KEYS": DynamicParameter("GEMINI_API_KEYS", [], list, allow_dsl=False, description="Comma-separated Gemini API keys pool"),
    "GEMINI_MODELS": DynamicParameter("GEMINI_MODELS", ["gemini-3.1-flash-lite"], list, allow_dsl=False, description="Comma-separated Gemini models rotation pool"),
    "THINKING_LEVEL": DynamicParameter("THINKING_LEVEL", "high", str, allow_dsl=False, description="Gemini reasoning level"),
    "TEMPERATURE": DynamicParameter("TEMPERATURE", 0.7, float, min_val=0.0, max_val=2.0, description="Model sampling temperature"),
    "TOP_P": DynamicParameter("TOP_P", 0.95, float, min_val=0.0, max_val=1.0, description="Model top_p nucleus sampling"),
    "STOP_SEQUENCES": DynamicParameter("STOP_SEQUENCES", [], list, allow_dsl=False, description="Model stop sequences"),
    "OUTPUT_LENGTH": DynamicParameter("OUTPUT_LENGTH", 65536, int, min_val=1, description="Max output tokens count"),
    "INPUT_TOKEN_LIMIT": DynamicParameter("INPUT_TOKEN_LIMIT", 524288, int, min_val=1000, description="Max input context token limit"),
    "SAFETY_HATE_SPEECH": DynamicParameter("SAFETY_HATE_SPEECH", "BLOCK_NONE", str, allow_dsl=False),
    "SAFETY_HARASSMENT": DynamicParameter("SAFETY_HARASSMENT", "BLOCK_NONE", str, allow_dsl=False),
    "SAFETY_SEXUALLY_EXPLICIT": DynamicParameter("SAFETY_SEXUALLY_EXPLICIT", "BLOCK_NONE", str, allow_dsl=False),
    "SAFETY_DANGEROUS_CONTENT": DynamicParameter("SAFETY_DANGEROUS_CONTENT", "BLOCK_NONE", str, allow_dsl=False),
    "CHARACTER_FILE": DynamicParameter("CHARACTER_FILE", "character.txt", str, allow_dsl=False),

    # --- 3. Generative Media & Pollinations ---
    "POLLINATIONS_KEYS": DynamicParameter("POLLINATIONS_KEYS", [], list, allow_dsl=False, description="Pollinations API keys pool"),
    "DEFAULT_IMAGE_MODEL": DynamicParameter("DEFAULT_IMAGE_MODEL", "flux", str, allow_dsl=False),
    "DEFAULT_IMAGE_WIDTH": DynamicParameter("DEFAULT_IMAGE_WIDTH", 1024, int, min_val=1),
    "DEFAULT_IMAGE_HEIGHT": DynamicParameter("DEFAULT_IMAGE_HEIGHT", 1024, int, min_val=1),
    "MEDIA_RESOLUTION": DynamicParameter("MEDIA_RESOLUTION", "high", str, allow_dsl=False),
    "ASPECT_RATIO": DynamicParameter("ASPECT_RATIO", "auto", str, allow_dsl=False),
    "GENERATE_IMAGE_TIMEOUT": DynamicParameter("GENERATE_IMAGE_TIMEOUT", 180.0, float, min_val=0.1),
    "DEFAULT_AUDIO_VOICE": DynamicParameter("DEFAULT_AUDIO_VOICE", "nova", str, allow_dsl=False),
    "DEFAULT_AUDIO_MODEL": DynamicParameter("DEFAULT_AUDIO_MODEL", "qwen-tts-instruct", str, allow_dsl=False),
    "GENERATE_AUDIO_TIMEOUT": DynamicParameter("GENERATE_AUDIO_TIMEOUT", 120.0, float, min_val=0.1),
    "DEFAULT_VIDEO_MODEL": DynamicParameter("DEFAULT_VIDEO_MODEL", "wan", str, allow_dsl=False),
    "DEFAULT_VIDEO_DURATION": DynamicParameter("DEFAULT_VIDEO_DURATION", 5, int, min_val=1),
    "DEFAULT_VIDEO_ASPECT_RATIO": DynamicParameter("DEFAULT_VIDEO_ASPECT_RATIO", "1:1", str, allow_dsl=False),
    "GENERATE_VIDEO_TIMEOUT": DynamicParameter("GENERATE_VIDEO_TIMEOUT", 180.0, float, min_val=0.1),
    "POLLINATIONS_SEED_MIN": DynamicParameter("POLLINATIONS_SEED_MIN", 1, int, min_val=1),
    "POLLINATIONS_SEED_MAX": DynamicParameter("POLLINATIONS_SEED_MAX", 999999999, int, min_val=1),
    "POLLINATIONS_UPLOAD_JPEG_QUALITY": DynamicParameter("POLLINATIONS_UPLOAD_JPEG_QUALITY", 95, int, min_val=1, max_val=100),
    "DEFAULT_PUBLIC_UPLOAD_PROVIDER": DynamicParameter("DEFAULT_PUBLIC_UPLOAD_PROVIDER", "auto", str, allow_dsl=False),
    "IMAGE_GEN_AUTO_DOWNLOAD": DynamicParameter("IMAGE_GEN_AUTO_DOWNLOAD", True, bool),
    "IMAGE_GEN_AUTO_UPLOAD_TO_GOOGLE": DynamicParameter("IMAGE_GEN_AUTO_UPLOAD_TO_GOOGLE", True, bool),
    "AUDIO_GEN_AUTO_DOWNLOAD": DynamicParameter("AUDIO_GEN_AUTO_DOWNLOAD", True, bool),
    "AUDIO_GEN_AUTO_UPLOAD_TO_GOOGLE": DynamicParameter("AUDIO_GEN_AUTO_UPLOAD_TO_GOOGLE", True, bool),
    "VIDEO_GEN_AUTO_DOWNLOAD": DynamicParameter("VIDEO_GEN_AUTO_DOWNLOAD", True, bool),
    "VIDEO_GEN_AUTO_UPLOAD_TO_GOOGLE": DynamicParameter("VIDEO_GEN_AUTO_UPLOAD_TO_GOOGLE", True, bool),
    "PUBLIC_UPLOAD_TIMEOUT": DynamicParameter("PUBLIC_UPLOAD_TIMEOUT", 60.0, float, min_val=0.1),

    # --- 4. Context, Token Strategies & Memory Modes ---
    "CONTEXT_MANAGEMENT_MODE": DynamicParameter("CONTEXT_MANAGEMENT_MODE", "summarize", str, allow_dsl=False, description="Text context strategy: summarize, trim, hybrid, none"),
    "TEXT_LIMIT_TYPE": DynamicParameter("TEXT_LIMIT_TYPE", "tokens", str, allow_dsl=False, description="Text limit type: tokens or messages_count"),
    "TEXT_TOKEN_LIMIT": DynamicParameter("TEXT_TOKEN_LIMIT", 524288, int, min_val=1000, description="Max text token budget"),
    "CONTEXT_TRIM_COUNT": DynamicParameter("CONTEXT_TRIM_COUNT", 20, int, min_val=1, description="Messages count to drop when trimming text context"),
    "MESSAGES_LIMIT": DynamicParameter("MESSAGES_LIMIT", 150, int, min_val=1),
    "CONTEXT_LOCAL_RATIO": DynamicParameter("CONTEXT_LOCAL_RATIO", 0.7, float, min_val=0.0, max_val=1.0),
    "CONTEXT_LOCAL_MIN_LIMIT": DynamicParameter("CONTEXT_LOCAL_MIN_LIMIT", 15, int, min_val=1),
    "SUMMARIZATION_MESSAGES_LIMIT": DynamicParameter("SUMMARIZATION_MESSAGES_LIMIT", 500, int, min_val=1),
    "SUMMARIZATION_KEEP_LIMIT": DynamicParameter("SUMMARIZATION_KEEP_LIMIT", 15, int, min_val=1),
    "CROSS_CHAT_CONTEXT": DynamicParameter("CROSS_CHAT_CONTEXT", True, bool),
    
    "FILE_CONTEXT_MODE": DynamicParameter("FILE_CONTEXT_MODE", "trim", str, allow_dsl=False, description="File context strategy: trim, summarize, hybrid, none"),
    "FILE_LIMIT_TYPE": DynamicParameter("FILE_LIMIT_TYPE", "tokens", str, allow_dsl=False, description="File limit type: tokens or files_count"),
    "FILE_TOKEN_LIMIT": DynamicParameter("FILE_TOKEN_LIMIT", 200000, int, min_val=1000, description="Max file token budget"),
    "FILE_TRIM_COUNT": DynamicParameter("FILE_TRIM_COUNT", 5, int, min_val=1, description="Files count to drop when trimming media context"),
    "MEDIA_LIMIT": DynamicParameter("MEDIA_LIMIT", 250, int, min_val=1),
    "AUTO_ATTACH_FILES_TO_CONTEXT": DynamicParameter("AUTO_ATTACH_FILES_TO_CONTEXT", False, bool, description="Auto attach media to Gemini context without explicit tool call"),

    # --- 5. Triggers, Rules & Flow Matrix ---
    "AUTO_SAVE_TEXT_RULE": DynamicParameter("AUTO_SAVE_TEXT_RULE", "all", str, description="DSL filter rule for auto saving text messages"),
    "AUTO_SAVE_FILE_RULE": DynamicParameter("AUTO_SAVE_FILE_RULE", "all", str, description="DSL filter rule for auto saving files"),
    "AI_RESPONSE_MODE": DynamicParameter("AI_RESPONSE_MODE", "all", str, allow_dsl=False, description="AI response scope: all, private_only, group_only, channel_only"),
    "AI_RESPONSE_TRIGGERS": DynamicParameter("AI_RESPONSE_TRIGGERS", ["name", "username", "mentioned", "reply_to_me"], list, allow_dsl=False),
    
    "SAVE_INCOMING_MESSAGES": DynamicParameter("SAVE_INCOMING_MESSAGES", True, bool),
    "SAVE_EDITED_MESSAGES": DynamicParameter("SAVE_EDITED_MESSAGES", True, bool),
    "SAVE_DELETED_MESSAGES": DynamicParameter("SAVE_DELETED_MESSAGES", True, bool),
    "SAVE_OUTGOING_NEW_MESSAGES": DynamicParameter("SAVE_OUTGOING_NEW_MESSAGES", True, bool),
    "SAVE_OUTGOING_EDITED_MESSAGES": DynamicParameter("SAVE_OUTGOING_EDITED_MESSAGES", True, bool),
    "SAVE_OUTGOING_DELETED_MESSAGES": DynamicParameter("SAVE_OUTGOING_DELETED_MESSAGES", True, bool),

    "TRIGGER_ON_INCOMING": DynamicParameter("TRIGGER_ON_INCOMING", True, bool),
    "TRIGGER_ON_EDITED": DynamicParameter("TRIGGER_ON_EDITED", False, bool),
    "TRIGGER_ON_DELETED": DynamicParameter("TRIGGER_ON_DELETED", False, bool),
    "TRIGGER_ON_OUTGOING_NEW_MESSAGES": DynamicParameter("TRIGGER_ON_OUTGOING_NEW_MESSAGES", False, bool),
    "TRIGGER_ON_OUTGOING_EDITED_MESSAGES": DynamicParameter("TRIGGER_ON_OUTGOING_EDITED_MESSAGES", False, bool),
    "TRIGGER_ON_OUTGOING_DELETED_MESSAGES": DynamicParameter("TRIGGER_ON_OUTGOING_DELETED_MESSAGES", False, bool),
    "TRIGGER_ON_OUTGOING_MANUAL_MESSAGES": DynamicParameter("TRIGGER_ON_OUTGOING_MANUAL_MESSAGES", False, bool, description="Trigger AI on manual outgoing messages"),
    "TRIGGER_ON_COMMANDS": DynamicParameter("TRIGGER_ON_COMMANDS", False, bool, description="Trigger AI generation on CLI commands"),

    "BOOTSTRAP_TRIGGER_GENERATION": DynamicParameter("BOOTSTRAP_TRIGGER_GENERATION", True, bool),
    "CATCH_UP_TRIGGER_GENERATION": DynamicParameter("CATCH_UP_TRIGGER_GENERATION", True, bool),
    "USE_SYSTEM_PROMPT": DynamicParameter("USE_SYSTEM_PROMPT", True, bool),

    # --- 6. Advanced Filters & Whitelists / Blacklists ---
    "FILTER_POLICY": DynamicParameter("FILTER_POLICY", "blacklist_first", str, allow_dsl=False),
    "MSG_SAVE_WHITELIST": DynamicParameter("MSG_SAVE_WHITELIST", [], list, allow_dsl=False),
    "MSG_SAVE_BLACKLIST": DynamicParameter("MSG_SAVE_BLACKLIST", [], list, allow_dsl=False),
    "MSG_GEN_WHITELIST": DynamicParameter("MSG_GEN_WHITELIST", [], list, allow_dsl=False),
    "MSG_GEN_BLACKLIST": DynamicParameter("MSG_GEN_BLACKLIST", [], list, allow_dsl=False),
    "ALLOWED_MESSAGE_TYPES": DynamicParameter("ALLOWED_MESSAGE_TYPES", ["text", "voice", "video", "photo", "document", "gif", "sticker", "location", "contact", "poll", "venue", "album", "list"], list, allow_dsl=False),

    "SAVE_INCOMING_REACTION_ADD": DynamicParameter("SAVE_INCOMING_REACTION_ADD", True, bool),
    "SAVE_INCOMING_REACTION_REMOVE": DynamicParameter("SAVE_INCOMING_REACTION_REMOVE", True, bool),
    "SAVE_OUTGOING_REACTION_ADD": DynamicParameter("SAVE_OUTGOING_REACTION_ADD", True, bool),
    "SAVE_OUTGOING_REACTION_REMOVE": DynamicParameter("SAVE_OUTGOING_REACTION_REMOVE", True, bool),
    "TRIGGER_ON_INCOMING_REACTION_ADD": DynamicParameter("TRIGGER_ON_INCOMING_REACTION_ADD", False, bool),
    "TRIGGER_ON_INCOMING_REACTION_REMOVE": DynamicParameter("TRIGGER_ON_INCOMING_REACTION_REMOVE", False, bool),
    "TRIGGER_ON_OUTGOING_REACTION_ADD": DynamicParameter("TRIGGER_ON_OUTGOING_REACTION_ADD", False, bool),
    "TRIGGER_ON_OUTGOING_REACTION_REMOVE": DynamicParameter("TRIGGER_ON_OUTGOING_REACTION_REMOVE", False, bool),
    "REACTION_WHITELIST": DynamicParameter("REACTION_WHITELIST", [], list, allow_dsl=False),
    "REACTION_BLACKLIST": DynamicParameter("REACTION_BLACKLIST", [], list, allow_dsl=False),

    "SAVE_USER_METADATA": DynamicParameter("SAVE_USER_METADATA", True, bool),
    "SAVE_CHAT_METADATA": DynamicParameter("SAVE_CHAT_METADATA", True, bool),
    "USER_CACHE_WHITELIST": DynamicParameter("USER_CACHE_WHITELIST", [], list, allow_dsl=False),
    "USER_CACHE_BLACKLIST": DynamicParameter("USER_CACHE_BLACKLIST", [], list, allow_dsl=False),
    "CHAT_CACHE_WHITELIST": DynamicParameter("CHAT_CACHE_WHITELIST", [], list, allow_dsl=False),
    "CHAT_CACHE_BLACKLIST": DynamicParameter("CHAT_CACHE_BLACKLIST", [], list, allow_dsl=False),

    "CHAT_WHITELIST": DynamicParameter("CHAT_WHITELIST", [], list, allow_dsl=False),
    "CHAT_BLACKLIST": DynamicParameter("CHAT_BLACKLIST", [], list, allow_dsl=False),
    "READ_ACK_WHITELIST": DynamicParameter("READ_ACK_WHITELIST", ["all"], list, allow_dsl=False),
    "READ_ACK_BLACKLIST": DynamicParameter("READ_ACK_BLACKLIST", [], list, allow_dsl=False),

    "AI_OUTPUT_WHITELIST_REGEX": DynamicParameter("AI_OUTPUT_WHITELIST_REGEX", [], list, allow_dsl=False),
    "AI_OUTPUT_BLACKLIST_REGEX": DynamicParameter("AI_OUTPUT_BLACKLIST_REGEX", [], list, allow_dsl=False),

    "AI_ALLOWED_ROOT_TOOLS": DynamicParameter("AI_ALLOWED_ROOT_TOOLS", ["all"], list, allow_dsl=False),
    "AI_BLOCKED_ROOT_TOOLS": DynamicParameter("AI_BLOCKED_ROOT_TOOLS", ["execute_python_code", "run_sandboxed_command"], list, allow_dsl=False),
    "AI_ALLOWED_CUSTOM_TOOLS": DynamicParameter("AI_ALLOWED_CUSTOM_TOOLS", ["all"], list, allow_dsl=False),
    "AI_BLOCKED_CUSTOM_TOOLS": DynamicParameter("AI_BLOCKED_CUSTOM_TOOLS", [], list, allow_dsl=False),
    "AI_ALLOWED_MIMES": DynamicParameter("AI_ALLOWED_MIMES", ["all"], list, allow_dsl=False),
    "AI_BLOCKED_MIMES": DynamicParameter("AI_BLOCKED_MIMES", ["none"], list, allow_dsl=False),

    # --- 7. AI Pipeline & Granular CRUD + INVOKE Permission Matrix ---
    "AI_ALLOW_PIPELINES": DynamicParameter("AI_ALLOW_PIPELINES", True, bool, description="Allow AI to chain tools/tags via pipeline operators"),
    "AI_ALLOWED_PIPELINE_OPERATORS": DynamicParameter("AI_ALLOWED_PIPELINE_OPERATORS", ";,&&,||,|", str, allow_dsl=False),
    "AI_BLOCKED_PIPELINE_OPERATORS": DynamicParameter("AI_BLOCKED_PIPELINE_OPERATORS", "", str, allow_dsl=False),

    "AI_PERM_COMMANDS_CREATE": DynamicParameter("AI_PERM_COMMANDS_CREATE", True, bool),
    "AI_PERM_COMMANDS_EDIT": DynamicParameter("AI_PERM_COMMANDS_EDIT", True, bool),
    "AI_PERM_COMMANDS_DELETE": DynamicParameter("AI_PERM_COMMANDS_DELETE", True, bool),
    "AI_PERM_COMMANDS_VIEW_INFO": DynamicParameter("AI_PERM_COMMANDS_VIEW_INFO", True, bool),
    "AI_PERM_COMMANDS_VIEW_CONTENT": DynamicParameter("AI_PERM_COMMANDS_VIEW_CONTENT", True, bool),
    "AI_PERM_COMMANDS_LIST": DynamicParameter("AI_PERM_COMMANDS_LIST", True, bool),
    "AI_PERM_COMMANDS_INVOKE": DynamicParameter("AI_PERM_COMMANDS_INVOKE", True, bool),

    "AI_PERM_TOOLS_CREATE": DynamicParameter("AI_PERM_TOOLS_CREATE", True, bool),
    "AI_PERM_TOOLS_EDIT": DynamicParameter("AI_PERM_TOOLS_EDIT", True, bool),
    "AI_PERM_TOOLS_DELETE": DynamicParameter("AI_PERM_TOOLS_DELETE", True, bool),
    "AI_PERM_TOOLS_VIEW_INFO": DynamicParameter("AI_PERM_TOOLS_VIEW_INFO", True, bool),
    "AI_PERM_TOOLS_VIEW_CONTENT": DynamicParameter("AI_PERM_TOOLS_VIEW_CONTENT", True, bool),
    "AI_PERM_TOOLS_LIST": DynamicParameter("AI_PERM_TOOLS_LIST", True, bool),
    "AI_PERM_TOOLS_INVOKE": DynamicParameter("AI_PERM_TOOLS_INVOKE", True, bool),

    "AI_PERM_TAGS_CREATE": DynamicParameter("AI_PERM_TAGS_CREATE", True, bool),
    "AI_PERM_TAGS_EDIT": DynamicParameter("AI_PERM_TAGS_EDIT", True, bool),
    "AI_PERM_TAGS_DELETE": DynamicParameter("AI_PERM_TAGS_DELETE", True, bool),
    "AI_PERM_TAGS_VIEW_INFO": DynamicParameter("AI_PERM_TAGS_VIEW_INFO", True, bool),
    "AI_PERM_TAGS_VIEW_CONTENT": DynamicParameter("AI_PERM_TAGS_VIEW_CONTENT", True, bool),
    "AI_PERM_TAGS_LIST": DynamicParameter("AI_PERM_TAGS_LIST", True, bool),
    "AI_PERM_TAGS_INVOKE": DynamicParameter("AI_PERM_TAGS_INVOKE", True, bool),

    "AI_PERM_SERVICES_CREATE": DynamicParameter("AI_PERM_SERVICES_CREATE", True, bool),
    "AI_PERM_SERVICES_EDIT": DynamicParameter("AI_PERM_SERVICES_EDIT", True, bool),
    "AI_PERM_SERVICES_DELETE": DynamicParameter("AI_PERM_SERVICES_DELETE", True, bool),
    "AI_PERM_SERVICES_VIEW_INFO": DynamicParameter("AI_PERM_SERVICES_VIEW_INFO", True, bool),
    "AI_PERM_SERVICES_VIEW_CONTENT": DynamicParameter("AI_PERM_SERVICES_VIEW_CONTENT", True, bool),
    "AI_PERM_SERVICES_LIST": DynamicParameter("AI_PERM_SERVICES_LIST", True, bool),
    "AI_PERM_SERVICES_INVOKE": DynamicParameter("AI_PERM_SERVICES_INVOKE", True, bool),

    "AI_PERM_CRON_CREATE": DynamicParameter("AI_PERM_CRON_CREATE", True, bool),
    "AI_PERM_CRON_EDIT": DynamicParameter("AI_PERM_CRON_EDIT", True, bool),
    "AI_PERM_CRON_DELETE": DynamicParameter("AI_PERM_CRON_DELETE", True, bool),
    "AI_PERM_CRON_VIEW_INFO": DynamicParameter("AI_PERM_CRON_VIEW_INFO", True, bool),
    "AI_PERM_CRON_VIEW_CONTENT": DynamicParameter("AI_PERM_CRON_VIEW_CONTENT", True, bool),
    "AI_PERM_CRON_LIST": DynamicParameter("AI_PERM_CRON_LIST", True, bool),
    "AI_PERM_CRON_INVOKE": DynamicParameter("AI_PERM_CRON_INVOKE", True, bool),

    "AI_PERM_SITES_CREATE": DynamicParameter("AI_PERM_SITES_CREATE", True, bool),
    "AI_PERM_SITES_EDIT": DynamicParameter("AI_PERM_SITES_EDIT", True, bool),
    "AI_PERM_SITES_DELETE": DynamicParameter("AI_PERM_SITES_DELETE", True, bool),
    "AI_PERM_SITES_VIEW_INFO": DynamicParameter("AI_PERM_SITES_VIEW_INFO", True, bool),
    "AI_PERM_SITES_VIEW_CONTENT": DynamicParameter("AI_PERM_SITES_VIEW_CONTENT", True, bool),
    "AI_PERM_SITES_LIST": DynamicParameter("AI_PERM_SITES_LIST", True, bool),
    "AI_PERM_SITES_INVOKE": DynamicParameter("AI_PERM_SITES_INVOKE", True, bool),

    # --- 8. Network, Limits, Timeouts & Intervals ---
    "TELEGRAM_CONNECT_TIMEOUT": DynamicParameter("TELEGRAM_CONNECT_TIMEOUT", 15.0, float, min_val=1.0),
    "TELEGRAM_CONNECTION_RETRIES": DynamicParameter("TELEGRAM_CONNECTION_RETRIES", 5, int, min_val=0),
    "TELEGRAM_RETRY_DELAY": DynamicParameter("TELEGRAM_RETRY_DELAY", 5.0, float, min_val=0.1),
    "TELEGRAM_AUTO_RECONNECT": DynamicParameter("TELEGRAM_AUTO_RECONNECT", True, bool),
    "TELEGRAM_TIMEOUT": DynamicParameter("TELEGRAM_TIMEOUT", 15.0, float, min_val=1.0),
    
    "TIMERS_LOOP_INTERVAL": DynamicParameter("TIMERS_LOOP_INTERVAL", 1.0, float, min_val=0.1),
    "KEEP_ALIVE_INTERVAL": DynamicParameter("KEEP_ALIVE_INTERVAL", 120, int, min_val=1),
    "CONNECTION_MONITOR_INTERVAL": DynamicParameter("CONNECTION_MONITOR_INTERVAL", 10, int, min_val=1),
    "GEMINI_TIMEOUT": DynamicParameter("GEMINI_TIMEOUT", 90.0, float, min_val=0.1),
    "TYPING_INTERVAL": DynamicParameter("TYPING_INTERVAL", 10.0, float, min_val=0.1),
    "TIMEOUT_SLEEP": DynamicParameter("TIMEOUT_SLEEP", 2.0, float, min_val=0.1),
    "QUEUE_PROMOTION_DELAY": DynamicParameter("QUEUE_PROMOTION_DELAY", 2.0, float, min_val=0.1),
    "RATE_LIMIT_SLEEP": DynamicParameter("RATE_LIMIT_SLEEP", 5.0, float, min_val=0.1),
    "API_ERROR_SLEEP": DynamicParameter("API_ERROR_SLEEP", 2.0, float, min_val=0.1),
    "PROFILE_UPDATE_INTERVAL": DynamicParameter("PROFILE_UPDATE_INTERVAL", 3600, int, min_val=1),
    "TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT": DynamicParameter("TELEGRAM_KEYBOARD_BUTTON_SEARCH_LIMIT", 10, int, min_val=1),
    "BOT_RESPONSE_TIMEOUT": DynamicParameter("BOT_RESPONSE_TIMEOUT", 6.0, float, min_val=0.1),
    "BUTTON_CLICK_TIMEOUT": DynamicParameter("BUTTON_CLICK_TIMEOUT", 15.0, float, min_val=0.1),
    "DOWNLOAD_MEDIA_TIMEOUT": DynamicParameter("DOWNLOAD_MEDIA_TIMEOUT", 120.0, float, min_val=0.1),
    "TELEGRAM_ACTION_TIMEOUT": DynamicParameter("TELEGRAM_ACTION_TIMEOUT", 60.0, float, min_val=0.1),
    "CONVERSION_TIMEOUT": DynamicParameter("CONVERSION_TIMEOUT", 30.0, float, min_val=0.1),
    "GOOGLE_UPLOAD_TIMEOUT": DynamicParameter("GOOGLE_UPLOAD_TIMEOUT", 120.0, float, min_val=0.1),
    "GEMINI_FREE_RECOVERY_TIME": DynamicParameter("GEMINI_FREE_RECOVERY_TIME", 18000, int, min_val=1),
    "GEMINI_PRO_RECOVERY_TIME": DynamicParameter("GEMINI_PRO_RECOVERY_TIME", 86400, int, min_val=1),
    "GEMINI_DEAD_KEY_COOLDOWN": DynamicParameter("GEMINI_DEAD_KEY_COOLDOWN", 31536000, int, min_val=1),
    "POLLINATIONS_KEY_RECOVERY_TIME": DynamicParameter("POLLINATIONS_KEY_RECOVERY_TIME", 3600, int, min_val=1),
    "KEY_INFO_TIMEOUT": DynamicParameter("KEY_INFO_TIMEOUT", 10.0, float, min_val=0.1),
    "MAX_FILE_SIZE": DynamicParameter("MAX_FILE_SIZE", 15 * 1024 * 1024, int, min_val=0),
    "DUPLICATE_CACHE_SIZE": DynamicParameter("DUPLICATE_CACHE_SIZE", 1000, int, min_val=1),
    "AVATAR_CACHE_TIME": DynamicParameter("AVATAR_CACHE_TIME", 86400, int, min_val=0),
    "DEFAULT_RESULT_INDEX": DynamicParameter("DEFAULT_RESULT_INDEX", 0, int),

    # --- 9. Proxy Pools & Tor Configuration ---
    "TELEGRAM_PROXIES": DynamicParameter("TELEGRAM_PROXIES", [], list, allow_dsl=False),
    "GEMINI_PROXIES": DynamicParameter("GEMINI_PROXIES", [], list, allow_dsl=False),
    "POLLINATIONS_PROXIES": DynamicParameter("POLLINATIONS_PROXIES", [], list, allow_dsl=False),
    "SCRAPER_PROXIES": DynamicParameter("SCRAPER_PROXIES", [], list, allow_dsl=False),
    "ALL_PROXY": DynamicParameter("ALL_PROXY", "", str, allow_dsl=False),
    "TOR_HOST": DynamicParameter("TOR_HOST", "127.0.0.1", str, allow_dsl=False),
    "TOR_SOCKS_PORT": DynamicParameter("TOR_SOCKS_PORT", 9050, int, min_val=1),
    "TOR_CONTROL_PORT": DynamicParameter("TOR_CONTROL_PORT", 9051, int, min_val=1),
    "TOR_PASSWORD": DynamicParameter("TOR_PASSWORD", "", str, allow_dsl=False),
    "TOR_ROTATION_TIMEOUT": DynamicParameter("TOR_ROTATION_TIMEOUT", 15.0, float, min_val=0.1),
    "POLLINATIONS_MAX_ATTEMPTS": DynamicParameter("POLLINATIONS_MAX_ATTEMPTS", 8, int, min_val=1),
    "TOR_MAX_CONSECUTIVE_FAILURES": DynamicParameter("TOR_MAX_CONSECUTIVE_FAILURES", 2, int, min_val=1),
    "PROXY_CHECK_TIMEOUT": DynamicParameter("PROXY_CHECK_TIMEOUT", 3.0, float, min_val=0.1),
    "PROXY_STRICT_CHECK": DynamicParameter("PROXY_STRICT_CHECK", False, bool),

    # --- 10. Sandbox, Scrapers, Commands & Security ---
    "SQL_SELECT_LIMIT": DynamicParameter("SQL_SELECT_LIMIT", 100, int, min_val=1),
    "SQL_STDOUT_CHAR_LIMIT": DynamicParameter("SQL_STDOUT_CHAR_LIMIT", 3500, int, min_val=1),
    "TELEGRAM_ACTION_CHAR_LIMIT": DynamicParameter("TELEGRAM_ACTION_CHAR_LIMIT", 5000, int, min_val=1),
    "TELEGRAM_ACTION_CONFIRM_LIMIT": DynamicParameter("TELEGRAM_ACTION_CONFIRM_LIMIT", 500, int, min_val=1),
    "VM_STDOUT_NOTICE_LIMIT": DynamicParameter("VM_STDOUT_NOTICE_LIMIT", 1500, int, min_val=1),
    "SANDBOX_COMMAND_CHAR_LIMIT": DynamicParameter("SANDBOX_COMMAND_CHAR_LIMIT", 3000, int, min_val=1),
    "SANDBOX_ALLOWED_FILES": DynamicParameter("SANDBOX_ALLOWED_FILES", "all", str),
    "SANDBOX_BLOCKED_FILES": DynamicParameter("SANDBOX_BLOCKED_FILES", "bot.py,config.py,db_manager.py,key_manager.py,gemini_manager.py,context_manager.py,permission_manager.py,service_manager.py,command_manager.py,prompt_interpolator.py,response_executor.py,sandbox.py,registry.py,utils.py,parser.py,downloader.py,proxy_manager.py,server.py,services.py,main.py,system_tools.py,file_tools.py,web_tools.py,telegram_tools.py,scheduler_tools.py,media_tools.py,site_tools.py,command_tools.py,service_tools.py,tag_block_tools.py,.env,.env.example,bot_context.db,bot_context.db-wal,bot_context.db-shm,baziliksina.session,baziliksina.session-journal,config.json,character.txt,system_prompt.txt,rules_prompt.txt,env_prompt.txt,summarize_prompt.txt,feedback_prompt.txt", str, allow_dsl=False),
    "WEB_SEARCH_RESULTS_LIMIT": DynamicParameter("WEB_SEARCH_RESULTS_LIMIT", 50, int, min_val=1),
    "WEB_MEDIA_SEARCH_RESULTS_LIMIT": DynamicParameter("WEB_MEDIA_SEARCH_RESULTS_LIMIT", 3, int, min_val=1, max_val=30),
    "WEB_MEDIA_SEARCH_CANDIDATES_LIMIT": DynamicParameter("WEB_MEDIA_SEARCH_CANDIDATES_LIMIT", 50, int, min_val=1, max_val=100),
    "WEB_DEEP_SEARCH_CANDIDATES_LIMIT": DynamicParameter("WEB_DEEP_SEARCH_CANDIDATES_LIMIT", 3, int, min_val=1, max_val=10),
    "WEB_DEEP_SEARCH_CHAR_LIMIT": DynamicParameter("WEB_DEEP_SEARCH_CHAR_LIMIT", 10000, int, min_val=1000, max_val=50000),
    "SCRAPE_CHAR_LIMIT": DynamicParameter("SCRAPE_CHAR_LIMIT", 4000, int, min_val=1),
    "WEB_SEARCH_TIMEOUT": DynamicParameter("WEB_SEARCH_TIMEOUT", 10.0, float, min_val=0.1),
    "WEB_MEDIA_SEARCH_TIMEOUT": DynamicParameter("WEB_MEDIA_SEARCH_TIMEOUT", 10.0, float, min_val=0.1),
    "SCRAPE_TIMEOUT": DynamicParameter("SCRAPE_TIMEOUT", 10.0, float, min_val=0.1),
    "MEDIA_SEARCH_AUTO_DOWNLOAD": DynamicParameter("MEDIA_SEARCH_AUTO_DOWNLOAD", True, bool),
    "MEDIA_SEARCH_AUTO_UPLOAD_TO_GOOGLE": DynamicParameter("MEDIA_SEARCH_AUTO_UPLOAD_TO_GOOGLE", True, bool),

    "SANDBOX_CONFIG_WHITELIST": DynamicParameter("SANDBOX_CONFIG_WHITELIST", ["all"], list, allow_dsl=False),
    "SANDBOX_CONFIG_BLACKLIST": DynamicParameter("SANDBOX_CONFIG_BLACKLIST", ["API_HASH", "TELEGRAM_API_HASH", "GEMINI_API_KEYS", "GEMINI_KEYS", "POLLINATIONS_KEYS", "TOR_PASSWORD", "ALL_PROXY", "all_proxy", "TELEGRAM_PROXIES", "GEMINI_PROXIES", "POLLINATIONS_PROXIES", "SCRAPER_PROXIES"], list, allow_dsl=False),
    "GAME_EMOJI_WHITELIST": DynamicParameter("GAME_EMOJI_WHITELIST", ["🎲", "🎯", "🎳", "🏀", "⚽", "🎰"], list, allow_dsl=False),
    "GAME_EMOJI_BLACKLIST": DynamicParameter("GAME_EMOJI_BLACKLIST", [], list, allow_dsl=False),
    "SANDBOX_COMMAND_WHITELIST": DynamicParameter("SANDBOX_COMMAND_WHITELIST", ["all"], list, allow_dsl=False),
    "SANDBOX_COMMAND_BLACKLIST": DynamicParameter("SANDBOX_COMMAND_BLACKLIST", ["rm", "sudo", "reboot", "shutdown", "init", "passwd", "chown", "chmod", "dd", "mkfs", "parted", "fdisk", "mkswap", "killall", "pkill", "kill", "mv", "systemctl", "service"], list, allow_dsl=False),
    "BOT_COMMAND_WHITELIST": DynamicParameter("BOT_COMMAND_WHITELIST", ["all"], list, allow_dsl=False),
    "BOT_COMMAND_BLACKLIST": DynamicParameter("BOT_COMMAND_BLACKLIST", [], list, allow_dsl=False),
    "OUTGOING_FILE_WHITELIST": DynamicParameter("OUTGOING_FILE_WHITELIST", ["all"], list, allow_dsl=False),
    "OUTGOING_FILE_BLACKLIST": DynamicParameter("OUTGOING_FILE_BLACKLIST", [], list, allow_dsl=False),
    "TELEGRAM_ACTION_WHITELIST": DynamicParameter("TELEGRAM_ACTION_WHITELIST", ["all"], list, allow_dsl=False),
    "TELEGRAM_ACTION_BLACKLIST": DynamicParameter("TELEGRAM_ACTION_BLACKLIST", ["log_out", "delete_account", "disconnect", "sign_in", "send_code_request", "switch_account"], list, allow_dsl=False),
    "SANDBOX_PYTHON_WHITELIST": DynamicParameter("SANDBOX_PYTHON_WHITELIST", ["all"], list, allow_dsl=False),
    "SANDBOX_PYTHON_BLACKLIST": DynamicParameter("SANDBOX_PYTHON_BLACKLIST", ["os.system", "os.popen", "subprocess", "shutil.rmtree", "eval", "exec"], list, allow_dsl=False),
    "INCOMING_FILE_WHITELIST": DynamicParameter("INCOMING_FILE_WHITELIST", ["all"], list, allow_dsl=False),
    "INCOMING_FILE_BLACKLIST": DynamicParameter("INCOMING_FILE_BLACKLIST", [], list, allow_dsl=False),
    "INLINE_CALLBACK_WHITELIST": DynamicParameter("INLINE_CALLBACK_WHITELIST", ["all"], list, allow_dsl=False),
    "INLINE_CALLBACK_BLACKLIST": DynamicParameter("INLINE_CALLBACK_BLACKLIST", [], list, allow_dsl=False),
    "KEYBOARD_BUTTON_WHITELIST": DynamicParameter("KEYBOARD_BUTTON_WHITELIST", ["all"], list, allow_dsl=False),
    "KEYBOARD_BUTTON_BLACKLIST": DynamicParameter("KEYBOARD_BUTTON_BLACKLIST", [], list, allow_dsl=False),
    "AI_TAG_WHITELIST": DynamicParameter("AI_TAG_WHITELIST", ["all"], list, allow_dsl=False),
    "AI_TAG_BLACKLIST": DynamicParameter("AI_TAG_BLACKLIST", [], list, allow_dsl=False),
    "AI_BLOCK_WHITELIST": DynamicParameter("AI_BLOCK_WHITELIST", ["all"], list, allow_dsl=False),
    "AI_BLOCK_BLACKLIST": DynamicParameter("AI_BLOCK_BLACKLIST", [], list, allow_dsl=False),
    "CUSTOM_TAG_BLOCK_CODE_WHITELIST": DynamicParameter("CUSTOM_TAG_BLOCK_CODE_WHITELIST", ["all"], list, allow_dsl=False),
    "CUSTOM_TAG_BLOCK_CODE_BLACKLIST": DynamicParameter("CUSTOM_TAG_BLOCK_CODE_BLACKLIST", [], list, allow_dsl=False),
    "GROUP_SETTINGS_WHITELIST": DynamicParameter("GROUP_SETTINGS_WHITELIST", ["all"], list, allow_dsl=False),
    "GROUP_SETTINGS_BLACKLIST": DynamicParameter("GROUP_SETTINGS_BLACKLIST", [], list, allow_dsl=False),
    "CONTACTS_MANAGE_WHITELIST": DynamicParameter("CONTACTS_MANAGE_WHITELIST", ["all"], list, allow_dsl=False),
    "CONTACTS_MANAGE_BLACKLIST": DynamicParameter("CONTACTS_MANAGE_BLACKLIST", [], list, allow_dsl=False),
    "ACCOUNT_SETTINGS_WHITELIST": DynamicParameter("ACCOUNT_SETTINGS_WHITELIST", ["all"], list, allow_dsl=False),
    "ACCOUNT_SETTINGS_BLACKLIST": DynamicParameter("ACCOUNT_SETTINGS_BLACKLIST", [], list, allow_dsl=False),

    # --- 11. RESTful Web Server Parameters ---
    "WEB_SERVER_ENABLE": DynamicParameter("WEB_SERVER_ENABLE", True, bool),
    "WEB_SERVER_HOST": DynamicParameter("WEB_SERVER_HOST", "0.0.0.0", str, allow_dsl=False),
    "WEB_SERVER_PORT": DynamicParameter("WEB_SERVER_PORT", 8080, int, min_val=1, max_val=65535),
    "WEB_SERVER_SUBDOMAIN": DynamicParameter("WEB_SERVER_SUBDOMAIN", "", str, allow_dsl=False),
    "WEB_SERVER_LOG_PATH": DynamicParameter("WEB_SERVER_LOG_PATH", "bot.log", str, allow_dsl=False),
    "WEB_SERVER_IP_ACL": DynamicParameter("WEB_SERVER_IP_ACL", [], list, allow_dsl=False),
    "WEB_SERVER_IP_DETECTION_HOST": DynamicParameter("WEB_SERVER_IP_DETECTION_HOST", "8.8.8.8", str, allow_dsl=False),
    "WEB_SERVER_IP_DETECTION_PORT": DynamicParameter("WEB_SERVER_IP_DETECTION_PORT", 80, int, min_val=1),
    "WEB_SERVER_DEFAULT_LOG_LIMIT": DynamicParameter("WEB_SERVER_DEFAULT_LOG_LIMIT", 150, int, min_val=1),
    "WEB_SERVER_DEFAULT_META_LIMIT": DynamicParameter("WEB_SERVER_DEFAULT_META_LIMIT", 50, int, min_val=1),
    "WEB_SERVER_DEFAULT_TIMER_DELAY": DynamicParameter("WEB_SERVER_DEFAULT_TIMER_DELAY", 60, int, min_val=1),
    "WEB_SERVER_REBOOT_DELAY": DynamicParameter("WEB_SERVER_REBOOT_DELAY", 2.0, float, min_val=0.1),
    "PACIFIC_STANDARD_TIME_OFFSET": DynamicParameter("PACIFIC_STANDARD_TIME_OFFSET", -8, int),
    "PACIFIC_DAYLIGHT_TIME_OFFSET": DynamicParameter("PACIFIC_DAYLIGHT_TIME_OFFSET", -7, int),
    "GEMINI_MIN_COOLDOWN_SECONDS": DynamicParameter("GEMINI_MIN_COOLDOWN_SECONDS", 5, int, min_val=1),
    "GEMINI_DAILY_LIMIT_COOLDOWN": DynamicParameter("GEMINI_DAILY_LIMIT_COOLDOWN", 86400, int, min_val=1),
    "RECURSIVE_REPLY_DEPTH_LIMIT": DynamicParameter("RECURSIVE_REPLY_DEPTH_LIMIT", 3, int, min_val=1),

    # --- 12. Dynamic Site Hosting Defaults ---
    "SITE_STORAGE_LIMIT_DEFAULT": DynamicParameter("SITE_STORAGE_LIMIT_DEFAULT", 10 * 1024 * 1024, int, min_val=1),
    "SITE_TIMEOUT_DEFAULT": DynamicParameter("SITE_TIMEOUT_DEFAULT", 5.0, float, min_val=0.1),
    "SITE_ALLOWED_IMPORTS_DEFAULT": DynamicParameter("SITE_ALLOWED_IMPORTS_DEFAULT", "json,math,random,urllib,hashlib,datetime", str, allow_dsl=False),
    "SITE_BLOCKED_IMPORTS_DEFAULT": DynamicParameter("SITE_BLOCKED_IMPORTS_DEFAULT", "os,sys,subprocess,shutil,builtins", str, allow_dsl=False),
    "SITE_ALLOWED_METHODS_DEFAULT": DynamicParameter("SITE_ALLOWED_METHODS_DEFAULT", "GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS,TRACE,QUERY,CONNECT,PRI", str, allow_dsl=False),
    "SITE_BLOCKED_METHODS_DEFAULT": DynamicParameter("SITE_BLOCKED_METHODS_DEFAULT", "", str, allow_dsl=False),
    "SITE_MAX_REQUEST_SIZE_DEFAULT": DynamicParameter("SITE_MAX_REQUEST_SIZE_DEFAULT", 1048576, int, min_val=1),
    "SITE_STORAGE_LIMIT_MAX": DynamicParameter("SITE_STORAGE_LIMIT_MAX", 52428800, int, min_val=1),
    "SITE_TIMEOUT_MAX": DynamicParameter("SITE_TIMEOUT_MAX", 30.0, float, min_val=0.1),

    "SANDBOX_COMMAND_REGEX_BLACKLIST": DynamicParameter("SANDBOX_COMMAND_REGEX_BLACKLIST", r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|\.session|\.db|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|context_manager\.py|permission_manager\.py|service_manager\.py|command_manager\.py|prompt_interpolator\.py|response_executor\.py|sandbox\.py|registry\.py|utils\.py|parser\.py|downloader\.py|proxy_manager\.py|server\.py|services\.py|main\.py|tools|core|database|services|server|utils|\.txt|\.json)", str, allow_dsl=False),
    "SANDBOX_COMMAND_REGEX_WHITELIST": DynamicParameter("SANDBOX_COMMAND_REGEX_WHITELIST", "", str, allow_dsl=False),
    "SITE_COMMAND_REGEX_BLACKLIST": DynamicParameter("SITE_COMMAND_REGEX_BLACKLIST", r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|\.session|\.db|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|context_manager\.py|permission_manager\.py|service_manager\.py|command_manager\.py|prompt_interpolator\.py|response_executor\.py|sandbox\.py|registry\.py|utils\.py|parser\.py|downloader\.py|proxy_manager\.py|server\.py|services\.py|main\.py|tools|core|database|services|server|utils|\.txt|\.json)", str, allow_dsl=False),
    "SITE_COMMAND_WHITELIST": DynamicParameter("SITE_COMMAND_WHITELIST", "all", str, allow_dsl=False),
    "SITE_COMMAND_REGEX_BLACKLIST": DynamicParameter("SITE_COMMAND_REGEX_BLACKLIST", r"\b(rm\s+-rf|sudo|reboot|shutdown|init|passwd|chown|chmod|dd|mkfs|parted|fdisk|mkswap|killall|pkill|kill\s+-9|mv\s+/|rm\s+/)\b|(\.env|bot\.py|config\.py|db_manager\.py|key_manager\.py|gemini_manager\.py|tools\.py|sandbox\.py|utils\.py|downloader\.py)", str, allow_dsl=False),
    "SITE_COMMAND_REGEX_WHITELIST": DynamicParameter("SITE_COMMAND_REGEX_WHITELIST", "", str, allow_dsl=False),
    "SITE_PYTHON_WHITELIST": DynamicParameter("SITE_PYTHON_WHITELIST", "all", str, allow_dsl=False),
    "SITE_PYTHON_BLACKLIST": DynamicParameter("SITE_PYTHON_BLACKLIST", "os.system,os.popen,subprocess,shutil.rmtree,eval,exec", str, allow_dsl=False),
    
    # Structural Assets & File Names
    "DB_NAME": DynamicParameter("DB_NAME", "bot_context.db", str, allow_dsl=False),
    "SQLITE_JOURNAL_MODE": DynamicParameter("SQLITE_JOURNAL_MODE", "WAL", str, allow_dsl=False),
    "EMOJI_CACHE_DIR_NAME": DynamicParameter("EMOJI_CACHE_DIR_NAME", "emoji_cache", str, allow_dsl=False),
    "AVATAR_CACHE_DIR_NAME": DynamicParameter("AVATAR_CACHE_DIR_NAME", "avatar_cache", str, allow_dsl=False),
    "GIFT_CACHE_DIR_NAME": DynamicParameter("GIFT_CACHE_DIR_NAME", "gift_cache", str, allow_dsl=False),
    "TEMP_MEDIA_DIR_NAME": DynamicParameter("TEMP_MEDIA_DIR_NAME", "temp_media", str, allow_dsl=False),
    "BOT_AVATAR_NAME": DynamicParameter("BOT_AVATAR_NAME", "bot_avatar.jpg", str, allow_dsl=False),
    "DEFAULT_IMAGE_NAME": DynamicParameter("DEFAULT_IMAGE_NAME", "generated_image.png", str, allow_dsl=False),
    "DEFAULT_AUDIO_NAME": DynamicParameter("DEFAULT_AUDIO_NAME", "generated_audio.mp3", str, allow_dsl=False),
    "DEFAULT_VIDEO_NAME": DynamicParameter("DEFAULT_VIDEO_NAME", "generated_video.mp4", str, allow_dsl=False),
}


# =====================================================================
# PATH CONSTANTS & HELPER FUNCTIONS
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

CONFIG_JSON_PATH = BASE_DIR / "config" / "config.json"


def check_proxy_active(proxy_url_str: str) -> bool:
    import socket
    import urllib.parse
    if not proxy_url_str:
        return False
    try:
        parsed = urllib.parse.urlparse(proxy_url_str)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return False
        timeout_val = float(_PARAMS["PROXY_CHECK_TIMEOUT"].evaluate()) if "PROXY_CHECK_TIMEOUT" in _PARAMS else 3.0
        with socket.create_connection((host, port), timeout=timeout_val):
            return True
    except Exception:
        return False


async def reload_config_from_db(db):
    """Loads dynamic database settings from SQLite and overrides active config parameters in memory."""
    try:
        db_settings = await db.get_all_settings()
        for key, val in db_settings.items():
            try:
                parsed_val = json.loads(val)
            except Exception:
                parsed_val = val
            
            if key in _PARAMS:
                _PARAMS[key].set_override(parsed_val)
            else:
                globals()[key] = parsed_val
        
        # Self-healing: Ensure administrative Web Server API key exists
        raw_keys = os.getenv("WEB_SERVER_API_KEYS", "")
        if raw_keys:
            try:
                globals()["WEB_SERVER_API_KEYS"] = json.loads(raw_keys)
            except Exception:
                pass
        
        if "WEB_SERVER_API_KEYS" not in globals() or not globals()["WEB_SERVER_API_KEYS"]:
            import secrets
            saved_key = await db.get_memory("web_server_persistent_admin_token")
            if not saved_key:
                saved_key = secrets.token_hex(24)
                await db.set_memory("web_server_persistent_admin_token", saved_key)
                logger.info(f"CRITICAL SECURITY: Generated secure admin token: {saved_key}")
            
            globals()["WEB_SERVER_API_KEYS"] = {
                saved_key: {"permissions": ["all"], "rate_limit": 100}
            }
        logger.info("Tier 4 Config Overwrite successfully synchronized with database settings!")
    except Exception as e:
        logger.error(f"Error reloading config from DB settings table: {str(e)}")


# Tier 3 config overwrite using config.json
if CONFIG_JSON_PATH.exists():
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            local_json = json.load(f)
        for k, v in local_json.items():
            if k in _PARAMS:
                _PARAMS[k].set_override(v)
            else:
                globals()[k] = v
        logger.info("Tier 3 Config Overwrite successfully completed using config.json.")
    except Exception as e:
        logger.error(f"Error loading Tier 3 config.json: {str(e)}")


# =====================================================================
# MODULE ATTRIBUTE INTERCEPTORS
# =====================================================================
def __getattr__(name: str) -> Any:
    """Intercepts module variable lookups and returns evaluated DynamicParameter primitives."""
    if name in _PARAMS:
        return _PARAMS[name].evaluate()
    if name in globals():
        return globals()[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

def __setattr__(name: str, value: Any):
    """Intercepts dynamic assignments to config variables."""
    if name in _PARAMS:
        _PARAMS[name].set_override(value)
    else:
        globals()[name] = value

def __dir__():
    """Allows standard Python autocompletion and inspection across all parameters."""
    return sorted(list(globals().keys()) + list(_PARAMS.keys()))

__all__ = sorted(list(globals().keys()) + list(_PARAMS.keys()))
