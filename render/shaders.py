"""OpenGL shader programs for the overlay renderer.

All rendering uses modern OpenGL (3.3+ core profile) with GLSL shaders.
"""
from pyglet.gl import (
    GL_VERTEX_SHADER, GL_FRAGMENT_SHADER, GL_COMPILE_STATUS,
    GL_LINK_STATUS, glCreateShader, glShaderSource,
    glCompileShader, glGetShaderiv, glGetShaderInfoLog,
    glCreateProgram, glAttachShader, glLinkProgram,
    glGetProgramiv, glGetProgramInfoLog,
    glDeleteShader, glDeleteProgram, glUseProgram,
    glUniform1f, glUniform2f, glUniform3f,
    glUniform4f, glUniform1i, glGetUniformLocation,
)


# ---- Default shaders (inlined for zero file I/O) ----

DEFAULT_VERTEX = """#version 330 core
layout(location = 0) in vec2 aPos;
layout(location = 1) in vec2 aTexCoord;

out vec2 TexCoord;

uniform vec2 uOffset;
uniform vec2 uScale;
uniform float uRotation;  // degrees
uniform vec2 uSize;       // element size for rotation pivot
uniform vec2 uScreenSize; // window size in pixels

void main() {
    // Apply scale and rotation around center
    vec2 centered = (aPos - 0.5) * uSize * uScale;
    float rad = radians(uRotation);
    float c = cos(rad);
    float s = sin(rad);
    vec2 rotated = vec2(
        centered.x * c - centered.y * s,
        centered.x * s + centered.y * c
    );
    vec2 pos = rotated + uOffset + uSize * 0.5;
    // Scene top-left → OpenGL NDC.
    // X: direct mapping (scene-left → NDC-left).  Bottom-up DIB handles
    //    the pipeline — no X flip needed.
    // Y: flipped — scene Y=0 (top) → NDC Y=+1 (top).
    vec2 ndc = vec2(
        (pos.x / uScreenSize.x) * 2.0 - 1.0,
        1.0 - (pos.y / uScreenSize.y) * 2.0
    );
    gl_Position = vec4(ndc, 0.0, 1.0);
    TexCoord = aTexCoord;
}
"""

DEFAULT_FRAGMENT = """#version 330 core
in vec2 TexCoord;

uniform sampler2D uTexture;
uniform float uOpacity;
uniform vec4 uColor;    // Multiply color (for tinting text)

out vec4 FragColor;

void main() {
    vec4 texColor = texture(uTexture, TexCoord);
    vec4 color = texColor * uColor;
    // Pre-multiplied alpha for UpdateLayeredWindow (AC_SRC_ALPHA)
    FragColor = vec4(color.rgb * color.a * uOpacity, color.a * uOpacity);
}
"""

# Circle mask shader — used for circular cover art
CIRCLE_MASK_FRAGMENT = """#version 330 core
in vec2 TexCoord;

uniform sampler2D uTexture;
uniform float uOpacity;
uniform float uFeather;     // edge feather in pixels
uniform vec2 uTexSize;
uniform vec3 uStrokeColor;
uniform float uStrokeWidth; // pixels, 0 = disabled

// -- Visual effects uniforms (0.0 = disabled, >0.0 = enabled) --
uniform float uCDGroove;       // intensity 0.0-1.0
uniform float uCDGrooveFreq;   // ring frequency (grooves per radius)
uniform float uMotionBlur;     // intensity 0.0-1.0 (tangential blur when rotating)
uniform float uSpecular;       // intensity 0.0-1.0 (glossy highlight)

out vec4 FragColor;

void main() {
    vec4 texColor = texture(uTexture, TexCoord);

    // Distance from center (0 at center, 1 at edge)
    vec2 center = vec2(0.5, 0.5);
    float dist = length(TexCoord - center) * 2.0;

    // ---- Visual Effects (applied to texColor before mask/stroke) ----

    // CD Groove: concentric ring reflection simulating CD/DVD surface
    if (uCDGroove > 0.0) {
        float ring = (sin(dist * uCDGrooveFreq * 6.28318) * 0.5 + 0.5);
        texColor.rgb *= 1.0 + (ring - 0.5) * uCDGroove * 0.4;
    }

    // Motion Blur: tangential blur — stronger near edges (rotation-agnostic)
    if (uMotionBlur > 0.0) {
        float blurPx = dist * dist * uMotionBlur * 22.0;
        float angle = atan(TexCoord.y - center.y, TexCoord.x - center.x);
        vec2 tang = vec2(-sin(angle), cos(angle));
        vec2 texel = tang / uTexSize;

        vec4 blurSum = texColor;
        float blurWeight = 1.0;
        for (int i = 1; i <= 3; i++) {
            float w = exp(-float(i*i) / max(blurPx * blurPx * 0.2, 0.001));
            vec2 off = texel * float(i) * blurPx;
            blurSum += texture(uTexture, TexCoord + off) * w;
            blurSum += texture(uTexture, TexCoord - off) * w;
            blurWeight += w * 2.0;
        }
        texColor = blurSum / blurWeight;
    }

    // Specular Highlight: Phong-like glossy CD surface reflection
    if (uSpecular > 0.0) {
        float nz = sqrt(clamp(1.0 - dist * dist, 0.0, 1.0));
        vec3 normal = normalize(vec3((TexCoord.x - center.x) * 2.0,
                                      (TexCoord.y - center.y) * 2.0, nz));
        vec3 lightDir = normalize(vec3(0.35, 0.45, 0.85));
        vec3 halfVec = normalize(lightDir + vec3(0.0, 0.0, 1.0));
        float spec = pow(max(dot(normal, halfVec), 0.0), 30.0);
        texColor.rgb += spec * uSpecular * 0.7;
    }

    // ---- Mask / stroke ----

    // Convert pixel feather to UV space (dist is 0..1)
    float featherUV = uFeather / uTexSize.x;

    // Circular mask with feather (minimum 1px AA built into uFeather)
    float maskAlpha = 1.0 - smoothstep(1.0 - featherUV, 1.0 + featherUV, dist);

    // Stroke ring: centered on the shape edge, with independent AA
    float strokeAlpha = 0.0;
    if (uStrokeWidth > 0.0) {
        float strokeUV = uStrokeWidth / uTexSize.x;
        float edgeDist = abs(dist - 1.0);
        float halfStroke = strokeUV * 0.5;
        // Stroke AA: use smaller of feather and 1.5px for crisp stroke edges
        float strokeAA = max(1.0 / uTexSize.x, min(featherUV, 2.0 / uTexSize.x));
        float innerEdge = max(0.0, halfStroke - strokeAA);
        float outerEdge = halfStroke + strokeAA;
        strokeAlpha = 1.0 - smoothstep(innerEdge, outerEdge, edgeDist);
    }

    // Pre-multiplied blend: cover texture + stroke ring
    float coverA = texColor.a * maskAlpha;
    float a = max(coverA, strokeAlpha) * uOpacity;
    vec3 rgb = (texColor.rgb * coverA + uStrokeColor * strokeAlpha) * uOpacity;
    FragColor = vec4(rgb, a);
}
"""

# Roundrect mask shader
ROUNDRECT_FRAGMENT = """#version 330 core
in vec2 TexCoord;

uniform sampler2D uTexture;
uniform float uOpacity;
uniform float uRadius;       // corner radius in pixels
uniform float uFeather;      // edge feather in pixels
uniform vec2 uTexSize;
uniform vec3 uStrokeColor;
uniform float uStrokeWidth;  // pixels, 0 = disabled

// -- Visual effects uniforms (0.0 = disabled, >0.0 = enabled) --
uniform float uCDGroove;       // intensity 0.0-1.0
uniform float uCDGrooveFreq;   // ring frequency (grooves per radius)
uniform float uMotionBlur;     // intensity 0.0-1.0 (tangential blur when rotating)
uniform float uSpecular;       // intensity 0.0-1.0 (glossy highlight)

out vec4 FragColor;

void main() {
    vec4 texColor = texture(uTexture, TexCoord);

    vec2 halfSize = uTexSize * 0.5;
    vec2 pos = TexCoord * uTexSize;

    // Compute signed distance to rounded rectangle (pixels)
    vec2 q = abs(pos - halfSize) - halfSize + uRadius;
    float dist = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - uRadius;

    // ---- Visual Effects (applied to texColor before mask/stroke) ----
    // Use UV-space distance from center for effects (shape-agnostic)
    vec2 uvCenter = halfSize / uTexSize;  // always (0.5, 0.5) for full-tex quads
    float fxDist = length((TexCoord - uvCenter) * (uTexSize / min(uTexSize.x, uTexSize.y))) * 2.0;

    // CD Groove: concentric ring reflection
    if (uCDGroove > 0.0) {
        float ring = (sin(fxDist * uCDGrooveFreq * 6.28318) * 0.5 + 0.5);
        texColor.rgb *= 1.0 + (ring - 0.5) * uCDGroove * 0.4;
    }

    // Motion Blur: tangential blur — stronger near edges
    if (uMotionBlur > 0.0) {
        float blurPx = fxDist * fxDist * uMotionBlur * 22.0;
        float angle = atan(TexCoord.y - uvCenter.y, TexCoord.x - uvCenter.x);
        vec2 tang = vec2(-sin(angle), cos(angle));
        vec2 texel = tang / uTexSize;

        vec4 blurSum = texColor;
        float blurWeight = 1.0;
        for (int i = 1; i <= 3; i++) {
            float w = exp(-float(i*i) / max(blurPx * blurPx * 0.2, 0.001));
            vec2 off = texel * float(i) * blurPx;
            blurSum += texture(uTexture, TexCoord + off) * w;
            blurSum += texture(uTexture, TexCoord - off) * w;
            blurWeight += w * 2.0;
        }
        texColor = blurSum / blurWeight;
    }

    // Specular Highlight: glossy surface reflection
    if (uSpecular > 0.0) {
        float nz = sqrt(clamp(1.0 - fxDist * fxDist, 0.0, 1.0));
        vec3 normal = normalize(vec3((TexCoord.x - uvCenter.x) * 2.0,
                                      (TexCoord.y - uvCenter.y) * 2.0, nz));
        vec3 lightDir = normalize(vec3(0.35, 0.45, 0.85));
        vec3 halfVec = normalize(lightDir + vec3(0.0, 0.0, 1.0));
        float spec = pow(max(dot(normal, halfVec), 0.0), 30.0);
        texColor.rgb += spec * uSpecular * 0.7;
    }

    // ---- Mask / stroke ----

    // Roundrect mask with feather (dist and uFeather both in pixels)
    float maskAlpha = 1.0 - smoothstep(-uFeather, uFeather, dist);

    // Stroke ring: centered on the shape edge (dist=0), independent AA
    float strokeAlpha = 0.0;
    if (uStrokeWidth > 0.0) {
        float edgeDist = abs(dist);
        float halfStroke = uStrokeWidth * 0.5;
        // Stroke AA: 1-2px, not tied to mask feather
        float strokeAA = max(1.0, min(uFeather, 2.0));
        float innerEdge = max(0.0, halfStroke - strokeAA);
        float outerEdge = halfStroke + strokeAA;
        strokeAlpha = 1.0 - smoothstep(innerEdge, outerEdge, edgeDist);
    }

    // Pre-multiplied blend: cover texture + stroke ring
    float coverA = texColor.a * maskAlpha;
    float a = max(coverA, strokeAlpha) * uOpacity;
    vec3 rgb = (texColor.rgb * coverA + uStrokeColor * strokeAlpha) * uOpacity;
    FragColor = vec4(rgb, a);
}
"""

# Blur shader (horizontal pass)
BLUR_HORIZONTAL = """#version 330 core
in vec2 TexCoord;

uniform sampler2D uTexture;
uniform float uBlurRadius;  // in pixels
uniform vec2 uTexSize;

out vec4 FragColor;

void main() {
    vec2 texelSize = 1.0 / uTexSize;
    float sigma = uBlurRadius / 3.0;
    float twoSigma2 = 2.0 * sigma * sigma;

    vec4 sum = vec4(0.0);
    float weightSum = 0.0;

    int radius = int(ceil(uBlurRadius));
    for (int x = -radius; x <= radius; x++) {
        float weight = exp(-float(x * x) / twoSigma2);
        vec2 offset = vec2(float(x) * texelSize.x, 0.0);
        sum += texture(uTexture, TexCoord + offset) * weight;
        weightSum += weight;
    }

    FragColor = sum / weightSum;
}
"""

# Blur shader (vertical pass)
BLUR_VERTICAL = """#version 330 core
in vec2 TexCoord;

uniform sampler2D uTexture;
uniform float uBlurRadius;
uniform vec2 uTexSize;

out vec4 FragColor;

void main() {
    vec2 texelSize = 1.0 / uTexSize;
    float sigma = uBlurRadius / 3.0;
    float twoSigma2 = 2.0 * sigma * sigma;

    vec4 sum = vec4(0.0);
    float weightSum = 0.0;

    int radius = int(ceil(uBlurRadius));
    for (int y = -radius; y <= radius; y++) {
        float weight = exp(-float(y * y) / twoSigma2);
        vec2 offset = vec2(0.0, float(y) * texelSize.y);
        sum += texture(uTexture, TexCoord + offset) * weight;
        weightSum += weight;
    }

    FragColor = sum / weightSum;
}
"""

# Glow shader (additive blend of blurred texture)
GLOW_FRAGMENT = """#version 330 core
in vec2 TexCoord;

uniform sampler2D uTexture;
uniform sampler2D uBlurTexture;
uniform float uGlowIntensity;
uniform float uOpacity;

out vec4 FragColor;

void main() {
    vec4 original = texture(uTexture, TexCoord);
    vec4 blur = texture(uBlurTexture, TexCoord);
    vec4 glow = blur * uGlowIntensity;

    // Additive blend
    vec3 result = original.rgb * original.a + glow.rgb * glow.a * 0.5;
    float alpha = max(original.a, glow.a * 0.3);

    FragColor = vec4(result, alpha * uOpacity);
}
"""


# ============================================================
# Shader program management
# ============================================================

class ShaderProgram:
    """Compiled and linked OpenGL shader program."""

    def __init__(self, vertex_src: str, fragment_src: str):
        self.program_id = self._compile(vertex_src, fragment_src)
        self._uniforms: dict[str, int] = {}

    def _compile(self, vertex_src: str, fragment_src: str) -> int:
        """Compile vertex+fragment shaders and link into program."""
        import ctypes

        vs = self._compile_shader(GL_VERTEX_SHADER, vertex_src)
        fs = self._compile_shader(GL_FRAGMENT_SHADER, fragment_src)

        program = glCreateProgram()
        glAttachShader(program, vs)
        glAttachShader(program, fs)
        glLinkProgram(program)

        # Check link status
        result = (ctypes.c_int * 1)(0)
        glGetProgramiv(program, GL_LINK_STATUS, result)
        if not result[0]:
            log_buf = ctypes.create_string_buffer(1024)
            length = (ctypes.c_int * 1)()
            glGetProgramInfoLog(program, 1024, length, log_buf)
            log_msg = log_buf.value.decode("utf-8", errors="replace") if log_buf.value else "unknown error"
            glDeleteShader(vs)
            glDeleteShader(fs)
            glDeleteProgram(program)
            raise RuntimeError(f"Shader link error: {log_msg}")

        glDeleteShader(vs)
        glDeleteShader(fs)
        return program

    @staticmethod
    def _compile_shader(shader_type: int, source: str) -> int:
        import ctypes
        from pyglet.gl import GLchar

        shader = glCreateShader(shader_type)

        # pyglet 2.x: glShaderSource takes (shader, count, POINTER(POINTER(GLchar)), lengths)
        src_bytes = source.encode("utf-8")
        # Create array of string pointers
        strings = (ctypes.c_char_p * 1)()
        strings[0] = ctypes.c_char_p(src_bytes)
        glShaderSource(shader, 1,
                       ctypes.cast(strings, ctypes.POINTER(ctypes.POINTER(GLchar))),
                       None)

        glCompileShader(shader)

        # Check compile status
        result = (ctypes.c_int * 1)(0)
        glGetShaderiv(shader, GL_COMPILE_STATUS, result)
        if not result[0]:
            # Get info log
            log_buf = ctypes.create_string_buffer(1024)
            length = (ctypes.c_int * 1)()
            glGetShaderInfoLog(shader, 1024, length, log_buf)
            log_msg = log_buf.value.decode("utf-8", errors="replace") if log_buf.value else "unknown error"
            glDeleteShader(shader)
            raise RuntimeError(f"Shader compile error: {log_msg}")
        return shader

    def use(self) -> None:
        glUseProgram(self.program_id)

    def uniform_location(self, name: str) -> int:
        if name not in self._uniforms:
            # pyglet 2.x: glGetUniformLocation expects bytes/ctypes, not str
            self._uniforms[name] = glGetUniformLocation(
                self.program_id, name.encode("utf-8"))
        return self._uniforms[name]

    def set_float(self, name: str, value: float) -> None:
        glUniform1f(self.uniform_location(name), value)

    def set_vec2(self, name: str, x: float, y: float) -> None:
        glUniform2f(self.uniform_location(name), x, y)

    def set_vec3(self, name: str, x: float, y: float, z: float) -> None:
        glUniform3f(self.uniform_location(name), x, y, z)

    def set_vec4(self, name: str, r: float, g: float, b: float, a: float) -> None:
        glUniform4f(self.uniform_location(name), r, g, b, a)

    def set_int(self, name: str, value: int) -> None:
        glUniform1i(self.uniform_location(name), value)

    def set_texture(self, name: str, texture_unit: int, texture_id: int) -> None:
        from pyglet.gl import glActiveTexture, GL_TEXTURE0, glBindTexture, GL_TEXTURE_2D
        glActiveTexture(GL_TEXTURE0 + texture_unit)
        glBindTexture(GL_TEXTURE_2D, texture_id)
        self.set_int(name, texture_unit)

    def __del__(self):
        try:
            from pyglet.gl import glDeleteProgram
            if self.program_id:
                glDeleteProgram(self.program_id)
        except Exception:
            pass


# Pre-compiled shader cache
_shader_cache: dict[str, ShaderProgram] = {}


def get_shader(name: str) -> ShaderProgram:
    """Get a named shader program (cached)."""
    if name in _shader_cache:
        return _shader_cache[name]

    if name == "default":
        prog = ShaderProgram(DEFAULT_VERTEX, DEFAULT_FRAGMENT)
    elif name == "circle_mask":
        prog = ShaderProgram(DEFAULT_VERTEX, CIRCLE_MASK_FRAGMENT)
    elif name == "roundrect_mask":
        prog = ShaderProgram(DEFAULT_VERTEX, ROUNDRECT_FRAGMENT)
    elif name == "blur_h":
        prog = ShaderProgram(DEFAULT_VERTEX, BLUR_HORIZONTAL)
    elif name == "blur_v":
        prog = ShaderProgram(DEFAULT_VERTEX, BLUR_VERTICAL)
    elif name == "glow":
        prog = ShaderProgram(DEFAULT_VERTEX, GLOW_FRAGMENT)
    else:
        raise ValueError(f"Unknown shader: {name}")

    _shader_cache[name] = prog
    return prog
