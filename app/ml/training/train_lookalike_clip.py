"""PLANNED: look-alike matching with CLIP image + title embeddings.

Not implemented yet: it needs catalogue images, titles and attributes.

Plan:
  1. Embed each listing's main image and title with a CLIP model (e.g. ViT-B/32).
  2. Filter candidates by category and hard attributes (fabric, size range, pack size).
  3. Nearest 30 neighbours by cosine similarity, drop near-duplicates of the same seller.
  4. Return p25 / median / p75 of their live prices as the look-alike band, plus the count
     ("crowded" = 10 or more strong look-alikes).
  5. Serve through the LookalikeModel interface (app/ml/interfaces.py); refresh nightly.
"""


def main() -> None:
    raise NotImplementedError("Needs catalogue images and titles. See the docstring for the plan.")


if __name__ == "__main__":
    main()
