#version 330 core
// Default fragment shader — samples a texture with opacity and tint.

in vec2 TexCoord;

uniform sampler2D uTexture;
uniform float uOpacity;
uniform vec4 uTint;  // Multiplicative tint color

out vec4 FragColor;

void main() {
    vec4 texColor = texture(uTexture, TexCoord);
    FragColor = texColor * uTint * vec4(1.0, 1.0, 1.0, uOpacity);
}
