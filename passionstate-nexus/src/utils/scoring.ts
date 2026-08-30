export function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

export function passThreshold(score: number, threshold: number): boolean {
  return score >= threshold;
}
