"""Easing functions for animation tweens.

All functions take t in [0, 1] and return a value in [0, 1].
Based on Robert Penner's easing equations.
"""
import math


def linear(t: float) -> float:
    return t


# ---- Quad ----
def ease_in_quad(t: float) -> float:
    return t * t


def ease_out_quad(t: float) -> float:
    return t * (2 - t)


def ease_in_out_quad(t: float) -> float:
    if t < 0.5:
        return 2 * t * t
    return -1 + (4 - 2 * t) * t


# ---- Cubic ----
def ease_in_cubic(t: float) -> float:
    return t * t * t


def ease_out_cubic(t: float) -> float:
    t1 = t - 1
    return t1 * t1 * t1 + 1


def ease_in_out_cubic(t: float) -> float:
    if t < 0.5:
        return 4 * t * t * t
    t1 = t - 1
    return 4 * t1 * t1 * t1 + 1


# ---- Sine ----
def ease_in_sine(t: float) -> float:
    return 1 - math.cos(t * math.pi / 2)


def ease_out_sine(t: float) -> float:
    return math.sin(t * math.pi / 2)


def ease_in_out_sine(t: float) -> float:
    return (1 - math.cos(t * math.pi)) / 2


# ---- Expo ----
def ease_in_expo(t: float) -> float:
    return 0 if t == 0 else 2 ** (10 * (t - 1))


def ease_out_expo(t: float) -> float:
    return 1 if t == 1 else 1 - 2 ** (-10 * t)


# ---- Back ----
def ease_out_back(t: float) -> float:
    c1 = 1.70158
    t1 = t - 1
    return t1 * t1 * ((c1 + 1) * t1 + c1) + 1


def ease_in_back(t: float) -> float:
    c1 = 1.70158
    return t * t * ((c1 + 1) * t - c1)


# ---- Elastic ----
def ease_out_elastic(t: float) -> float:
    if t == 0 or t == 1:
        return t
    return 2 ** (-10 * t) * math.sin((t - 0.075) * (2 * math.pi) / 0.3) + 1


# ---- Bounce ----
def ease_out_bounce(t: float) -> float:
    if t < 1 / 2.75:
        return 7.5625 * t * t
    elif t < 2 / 2.75:
        t1 = t - 1.5 / 2.75
        return 7.5625 * t1 * t1 + 0.75
    elif t < 2.5 / 2.75:
        t1 = t - 2.25 / 2.75
        return 7.5625 * t1 * t1 + 0.9375
    else:
        t1 = t - 2.625 / 2.75
        return 7.5625 * t1 * t1 + 0.984375


def ease_in_bounce(t: float) -> float:
    return 1 - ease_out_bounce(1 - t)


# Lookup table
EASING_FUNCTIONS = {
    "linear": linear,
    "ease-in": ease_in_quad,
    "ease-out": ease_out_quad,
    "ease-in-out": ease_in_out_quad,
    "ease-in-cubic": ease_in_cubic,
    "ease-out-cubic": ease_out_cubic,
    "ease-in-out-cubic": ease_in_out_cubic,
    "ease-in-sine": ease_in_sine,
    "ease-out-sine": ease_out_sine,
    "ease-in-out-sine": ease_in_out_sine,
    "ease-in-expo": ease_in_expo,
    "ease-out-expo": ease_out_expo,
    "ease-in-back": ease_in_back,
    "ease-out-back": ease_out_back,
    "ease-out-elastic": ease_out_elastic,
    "ease-out-bounce": ease_out_bounce,
    "ease-in-bounce": ease_in_bounce,
}


def get_easing(name: str):
    """Get easing function by name. Falls back to linear if not found."""
    return EASING_FUNCTIONS.get(name, linear)
