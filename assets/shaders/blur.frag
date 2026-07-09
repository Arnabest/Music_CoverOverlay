#version 330 core
// Two-pass Gaussian blur fragment shader.
// Pass 1: horizontal blur (set uDirection = vec2(1, 0))
// Pass 2: vertical blur (set uDirection = vec2(0, 1))
// The two passes together produce a separable Gaussian blur.

in vec2 TexCoord;

uniform sampler2D uTexture;
uniform float uRadius;     // Blur radius in pixels
uniform vec2 uTexSize;     // Texture size in pixels
uniform vec2 uDirection;   // (1,0) for horizontal, (0,1) for vertical

out vec4 FragColor;

void main() {
    vec2 texelSize = 1.0 / uTexSize;
    vec2 dir = uDirection * texelSize;

    // Gaussian weights (sigma = radius / 3)
    float sigma = max(uRadius / 3.0, 0.001);
    float twoSigma2 = 2.0 * sigma * sigma;

    vec4 sum = vec4(0.0);
    float weightSum = 0.0;

    int radius = int(ceil(uRadius));
    for (int i = -radius; i <= radius; i++) {
        float weight = exp(-float(i * i) / twoSigma2);
        vec2 offset = dir * float(i);
        sum += texture(uTexture, TexCoord + offset) * weight;
        weightSum += weight;
    }

    FragColor = sum / weightSum;
}
