document.addEventListener("DOMContentLoaded", () => {
  console.log("E-commerce frontend loaded.");

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-product-gallery]").forEach((gallery) => {
    const mainImage = gallery.querySelector("[data-gallery-main]");
    gallery.querySelectorAll("[data-gallery-thumbnail]").forEach((thumbnail) => {
      thumbnail.addEventListener("click", () => {
        if (!mainImage) return;
        mainImage.src = thumbnail.dataset.imageSrc;
        mainImage.alt = thumbnail.dataset.imageAlt;
        gallery.querySelectorAll("[data-gallery-thumbnail]").forEach((item) => {
          item.classList.remove("is-selected");
          item.removeAttribute("aria-current");
        });
        thumbnail.classList.add("is-selected");
        thumbnail.setAttribute("aria-current", "true");
      });
    });
  });

  const imageInput = document.querySelector("#product-image-files");
  if (!imageInput) return;

  const gallery = imageInput.closest(".admin-product-gallery");
  const previews = document.querySelector("#new-product-image-previews");
  const count = document.querySelector("#product-image-count");
  const existingCards = document.querySelector("#existing-product-images");

  const selectedFiles = () => Array.from(imageInput.files || []);
  const remainingImageCount = () => {
    const cards = existingCards
      ? Array.from(existingCards.querySelectorAll(".admin-gallery-card"))
      : [];
    return cards.filter((card) => {
      const deleteCheckbox = card.querySelector('input[name="delete_image_ids"]');
      return !deleteCheckbox || !deleteCheckbox.checked;
    }).length;
  };
  const updateCount = () => {
    count.textContent = `Đang giữ ${remainingImageCount()} ảnh, đã chọn ${selectedFiles().length} ảnh mới`;
  };
  const renderPreviews = () => {
    const selectedPrimary = document.querySelector('input[name="primary_image"]:checked');
    const selectedPrimaryValue = selectedPrimary ? selectedPrimary.value : "";
    previews.replaceChildren();
    const files = selectedFiles();
    const usedPositions = new Set();
    if (existingCards) {
      existingCards.querySelectorAll(".admin-gallery-card").forEach((card) => {
        const deleted = card.querySelector('input[name="delete_image_ids"]:checked');
        const order = card.querySelector('select[name^="image_order_"]');
        if (!deleted && order) usedPositions.add(order.value);
      });
    }

    files.forEach((file, index) => {
      const availablePosition = Array.from({ length: 5 }, (_, position) => String(position + 1))
        .find((position) => !usedPositions.has(position));
      const position = availablePosition || "1";
      usedPositions.add(position);
      const card = document.createElement("article");
      card.className = "admin-gallery-card";
      const image = document.createElement("img");
      image.alt = `Ảnh xem trước ${index + 1}`;
      image.src = URL.createObjectURL(file);
      image.addEventListener("load", () => URL.revokeObjectURL(image.src), { once: true });

      const primaryLabel = document.createElement("label");
      primaryLabel.className = "admin-primary-choice";
      const primary = document.createElement("input");
      primary.type = "radio";
      primary.name = "primary_image";
      primary.value = `new:${index}`;
      primary.checked = selectedPrimaryValue
        ? selectedPrimaryValue === primary.value
        : remainingImageCount() === 0 && index === 0;
      primaryLabel.append(primary, "  Chọn làm ảnh chính");

      const orderLabel = document.createElement("label");
      orderLabel.textContent = "Thứ tự";
      const order = document.createElement("select");
      order.name = `image_order_new_${index}`;
      for (let slot = 1; slot <= 5; slot += 1) {
        const option = document.createElement("option");
        option.value = String(slot);
        option.textContent = String(slot);
        option.selected = option.value === position;
        order.append(option);
      }
      orderLabel.append(order);
      card.append(image, primaryLabel, orderLabel);
      previews.append(card);
    });
    updateCount();
  };

  imageInput.addEventListener("change", () => {
    renderPreviews();
  });

  if (existingCards) {
    existingCards.querySelectorAll('input[name="delete_image_ids"]').forEach((checkbox) => {
      checkbox.addEventListener("change", renderPreviews);
    });
  }
  updateCount();
});
