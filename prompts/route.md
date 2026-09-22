## Route

- A work order carries the unknowns it resolves, a bucket, and its dependencies — nothing about method. The bucket is the mode of the work, priced in points:

| bucket | points | mode | the method is |
|---|---|---|---|
| low | 3 | implement | known — do it |
| medium | 8 | validate | one of a couple of options — weigh them, conclude, do it |
| high | 21 | explore | unknown — try several in parallel before choosing |

- The controller buckets by how much is unknown about the method; the worker reads the bucket as how wide to look.
