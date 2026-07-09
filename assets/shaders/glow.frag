#version 330 core
// Glow effect — combines original texture with a blurred version.
// The blurred texture is sampled from uBlurTexture (result of two-pass blur).
// Additive blend creates the glow effect.

in vec2 TexCoord;

uniform sampler2D uTexture;      // Original sharp image
uniform sampler2D uBlurTexture;  // Pre-blurred version
uniform float uIntensity;        // Glow intensity (0.0 - 2.0)
uniform float uOpacity;          // Final opacity

out vec4 FragColor;

void main() {
    vec4 original = texture(uTexture, TexCoord);
    vec4 blur = texture(uBlurTexture, TexCoord);

    // Glow: add blurred version on top
    vec3 glow = blur.rgb * blur.a * uIntensity;

    // Composite: original on top of glow
    vec3 result = original.rgb * original.a + glow * (1.0 - original.a) * 0.5;
    float alpha = max(original.a, blur.a * uIntensity * 0.4);

    FragColor = vec4(result, alpha * uOpacity);
}
