#version 330 core
// Default vertex shader — positions a textured quad with offset, scale, and rotation.

layout(location = 0) in vec2 aPos;
layout(location = 1) in vec2 aTexCoord;

out vec2 TexCoord;

uniform vec2 uOffset;      // Top-left position of the element (in window pixels)
uniform vec2 uScale;       // Scale factors (x, y)
uniform float uRotation;   // Rotation in degrees
uniform vec2 uSize;        // Element size in pixels (for rotation pivot)
uniform vec2 uScreenSize;  // Window size in pixels (for NDC conversion)

void main() {
    // Center the quad around its own center
    vec2 centered = (aPos - 0.5) * uSize;

    // Apply scale
    centered *= uScale;

    // Apply rotation around element center
    float rad = radians(uRotation);
    float c = cos(rad);
    float s = sin(rad);
    vec2 rotated = vec2(
        centered.x * c - centered.y * s,
        centered.x * s + centered.y * c
    );

    // Position element at its offset in window space
    vec2 pos = rotated + uOffset + uSize * 0.5;

    // Convert to NDC (-1 to 1)
    // Note: Y is flipped because window coords have origin at top-left
    vec2 ndc = vec2(
        (pos.x / uScreenSize.x) * 2.0 - 1.0,
        1.0 - (pos.y / uScreenSize.y) * 2.0
    );

    gl_Position = vec4(ndc, 0.0, 1.0);
    TexCoord = aTexCoord;
}
