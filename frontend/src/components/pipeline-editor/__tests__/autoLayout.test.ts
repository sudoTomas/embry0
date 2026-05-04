import { describe, expect, it } from 'vitest';
import type { Edge, Node } from '@xyflow/react';
import { autoLayout } from '../autoLayout';

const node = (id: string, x = 0, y = 0): Node => ({
  id,
  position: { x, y },
  data: { agentType: 'developer', label: id },
  type: 'agentNode',
});

describe('autoLayout', () => {
  it('returns an empty array when given no nodes', () => {
    expect(autoLayout([], [])).toEqual([]);
  });

  it('preserves a single node with a numeric position', () => {
    const result = autoLayout([node('a', 999, 999)], []);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('a');
    expect(typeof result[0].position.x).toBe('number');
    expect(typeof result[0].position.y).toBe('number');
  });

  it('lays out a→b left-to-right', () => {
    const nodes = [node('a'), node('b')];
    const edges: Edge[] = [{ id: 'e1', source: 'a', target: 'b' }];
    const result = autoLayout(nodes, edges, 'LR');
    const a = result.find((n) => n.id === 'a')!;
    const b = result.find((n) => n.id === 'b')!;
    expect(b.position.x).toBeGreaterThan(a.position.x);
  });

  it('lays out a→b→c left-to-right with c→a feedback edge ignored', () => {
    const nodes = [node('a'), node('b'), node('c')];
    const edges: Edge[] = [
      { id: 'e1', source: 'a', target: 'b' },
      { id: 'e2', source: 'b', target: 'c' },
      { id: 'e3', source: 'c', target: 'a', type: 'feedbackEdge' },
    ];
    const result = autoLayout(nodes, edges, 'LR');
    const xs = Object.fromEntries(result.map((n) => [n.id, n.position.x]));
    expect(xs.b).toBeGreaterThan(xs.a);
    expect(xs.c).toBeGreaterThan(xs.b);
  });

  it('does not mutate the input nodes', () => {
    const original = node('a', 100, 200);
    const result = autoLayout([original], []);
    expect(original.position).toEqual({ x: 100, y: 200 });
    expect(result[0]).not.toBe(original);
  });
});
