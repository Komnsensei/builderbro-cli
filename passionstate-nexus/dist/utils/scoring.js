export function average(values) {
    if (values.length === 0)
        return 0;
    return values.reduce((sum, v) => sum + v, 0) / values.length;
}
export function passThreshold(score, threshold) {
    return score >= threshold;
}
