(function () {
  'use strict';

  var selectSequence = 0;
  var openSelect = null;

  function enabledOptions(widget) {
    return widget.options.filter(function (item) {
      return !item.option.disabled && !item.option.hidden;
    });
  }

  function positionMenu(widget) {
    if (!widget || widget.menu.hidden) return;

    var rect = widget.trigger.getBoundingClientRect();
    var viewportGap = 12;
    var menuGap = 6;
    var spaceBelow = window.innerHeight - rect.bottom - viewportGap;
    var spaceAbove = rect.top - viewportGap;
    var openAbove = spaceBelow < 190 && spaceAbove > spaceBelow;
    var availableHeight = Math.max(120, Math.min(280, openAbove ? spaceAbove : spaceBelow));
    var menuWidth = Math.min(rect.width, window.innerWidth - viewportGap * 2);
    var menuLeft = Math.min(
      Math.max(viewportGap, rect.left),
      window.innerWidth - viewportGap - menuWidth
    );

    widget.menu.style.left = menuLeft + 'px';
    widget.menu.style.width = menuWidth + 'px';
    widget.menu.style.maxHeight = availableHeight + 'px';
    widget.menu.style.top = openAbove
      ? Math.max(viewportGap, rect.top - Math.min(widget.menu.scrollHeight, availableHeight) - menuGap) + 'px'
      : Math.min(window.innerHeight - viewportGap, rect.bottom + menuGap) + 'px';
  }

  function closeSelect(widget, restoreFocus) {
    if (!widget) return;
    widget.wrapper.classList.remove('is-open');
    widget.trigger.setAttribute('aria-expanded', 'false');
    widget.menu.hidden = true;
    if (openSelect === widget) openSelect = null;
    if (restoreFocus) widget.trigger.focus();
  }

  function focusOption(widget, optionItem) {
    if (!optionItem) return;
    optionItem.element.focus();
    optionItem.element.scrollIntoView({block: 'nearest'});
  }

  function openSelectMenu(widget, focusSelected) {
    if (widget.select.disabled) return;
    if (openSelect && openSelect !== widget) closeSelect(openSelect, false);

    openSelect = widget;
    widget.wrapper.classList.add('is-open');
    widget.trigger.setAttribute('aria-expanded', 'true');
    widget.menu.hidden = false;
    positionMenu(widget);

    if (focusSelected) {
      var selected = widget.options.find(function (item) {
        return item.option.selected && !item.option.disabled;
      });
      focusOption(widget, selected || enabledOptions(widget)[0]);
    }
  }

  function syncSelect(widget) {
    var selected = widget.select.options[widget.select.selectedIndex];
    widget.triggerLabel.textContent = selected ? selected.textContent.trim() : 'Выберите значение';
    widget.trigger.classList.toggle('is-placeholder', !selected || selected.value === '');
    widget.trigger.disabled = widget.select.disabled;
    widget.trigger.setAttribute('aria-disabled', widget.select.disabled ? 'true' : 'false');
    if (widget.select.validity.valid) widget.wrapper.classList.remove('is-invalid');

    widget.options.forEach(function (item) {
      var isSelected = item.option.selected;
      item.element.classList.toggle('is-selected', isSelected);
      item.element.setAttribute('aria-selected', isSelected ? 'true' : 'false');
    });
  }

  function chooseOption(widget, item) {
    if (item.option.disabled) return;
    var changed = widget.select.value !== item.option.value;
    widget.select.value = item.option.value;
    syncSelect(widget);
    closeSelect(widget, true);

    if (changed) {
      widget.select.dispatchEvent(new Event('change', {bubbles: true}));
    }
  }

  function handleTriggerKeydown(event, widget) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      openSelectMenu(widget, true);
      if (event.key === 'ArrowUp') {
        var available = enabledOptions(widget);
        focusOption(widget, available[available.length - 1]);
      }
      return;
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (widget.menu.hidden) openSelectMenu(widget, true);
      else closeSelect(widget, false);
    }
  }

  function handleOptionKeydown(event, widget, item) {
    var available = enabledOptions(widget);
    var currentIndex = available.indexOf(item);

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      var direction = event.key === 'ArrowDown' ? 1 : -1;
      var nextIndex = (currentIndex + direction + available.length) % available.length;
      focusOption(widget, available[nextIndex]);
      return;
    }

    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      focusOption(widget, event.key === 'Home' ? available[0] : available[available.length - 1]);
      return;
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      chooseOption(widget, item);
      return;
    }

    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closeSelect(widget, true);
      return;
    }

    if (event.key === 'Tab') {
      closeSelect(widget, false);
    }
  }

  function buildOptions(widget) {
    widget.menu.innerHTML = '';
    widget.options = Array.prototype.map.call(widget.select.options, function (option, index) {
      var item = document.createElement('div');
      item.className = 'custom-select__option';
      item.id = widget.menu.id + '-option-' + index;
      item.setAttribute('role', 'option');
      item.setAttribute('tabindex', '-1');
      item.setAttribute('aria-selected', option.selected ? 'true' : 'false');
      item.textContent = option.textContent.trim();

      if (option.disabled) {
        item.classList.add('is-disabled');
        item.setAttribute('aria-disabled', 'true');
      } else {
        item.addEventListener('click', function () {
          chooseOption(widget, optionItem);
        });
        item.addEventListener('keydown', function (event) {
          handleOptionKeydown(event, widget, optionItem);
        });
      }

      var optionItem = {option: option, element: item};
      widget.menu.appendChild(item);
      return optionItem;
    });
  }

  function destroySelect(widget) {
    if (!widget) return;
    if (openSelect === widget) openSelect = null;
    widget.menu.remove();
  }

  function concealNativeSelect(select) {
    select.classList.add('custom-select__native');
    select.setAttribute('aria-hidden', 'true');
    select.setAttribute('tabindex', '-1');
    select.inert = true;
  }

  function enhanceSelect(select) {
    if (select.multiple) return;
    if (select.dataset.customSelectEnhanced === 'true') {
      if (select._customSelect) {
        concealNativeSelect(select);
        syncSelect(select._customSelect);
        return;
      }
      delete select.dataset.customSelectEnhanced;
    }

    selectSequence += 1;
    var wrapper = document.createElement('div');
    var trigger = document.createElement('button');
    var triggerLabel = document.createElement('span');
    var menu = document.createElement('div');
    var menuId = 'custom-select-menu-' + selectSequence;
    var triggerId = menuId + '-trigger';
    var accessibleLabel = select.getAttribute('aria-label') || '';

    if (!accessibleLabel && select.id) {
      Array.prototype.some.call(document.querySelectorAll('label'), function (label) {
        if (label.htmlFor !== select.id) return false;
        accessibleLabel = label.textContent.trim();
        return true;
      });
    }
    if (!accessibleLabel && select.closest('label')) {
      var wrappingLabel = select.closest('label');
      var hiddenLabel = wrappingLabel.querySelector('.sr-only');
      accessibleLabel = (hiddenLabel || wrappingLabel).textContent.trim();
    }
    if (!accessibleLabel) accessibleLabel = select.name || 'Выбор значения';

    wrapper.className = 'custom-select';
    if (select.classList.contains('status-select')) {
      wrapper.classList.add('custom-select--compact');
    }

    trigger.type = 'button';
    trigger.id = triggerId;
    trigger.className = 'custom-select__trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-controls', menuId);
    trigger.setAttribute('aria-label', accessibleLabel);
    triggerLabel.className = 'custom-select__value';
    trigger.appendChild(triggerLabel);

    menu.id = menuId;
    menu.className = 'custom-select__menu scroll-area';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-labelledby', triggerId);
    menu.hidden = true;

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);
    wrapper.appendChild(trigger);
    document.body.appendChild(menu);

    concealNativeSelect(select);
    select.dataset.customSelectEnhanced = 'true';

    var widget = {
      select: select,
      wrapper: wrapper,
      trigger: trigger,
      triggerLabel: triggerLabel,
      menu: menu,
      options: []
    };
    select._customSelect = widget;
    wrapper._customSelect = widget;

    buildOptions(widget);
    syncSelect(widget);

    trigger.addEventListener('click', function () {
      if (menu.hidden) openSelectMenu(widget, false);
      else closeSelect(widget, false);
    });
    trigger.addEventListener('keydown', function (event) {
      handleTriggerKeydown(event, widget);
    });
    select.addEventListener('change', function () {
      syncSelect(widget);
    });
    select.addEventListener('invalid', function () {
      wrapper.classList.add('is-invalid');
      window.setTimeout(function () {
        trigger.focus();
      }, 0);
    });
  }

  function enhanceSelects(root) {
    var scope = root && root.querySelectorAll ? root : document;
    if (root && root.matches && root.matches('select[data-custom-select="true"]')) {
      enhanceSelect(root);
    }
    scope.querySelectorAll('select[data-custom-select="true"]').forEach(enhanceSelect);
  }

  function enhanceTextareas(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var textareas = [];
    if (root && root.matches && root.matches('.chat__composer textarea')) {
      textareas.push(root);
    }
    scope.querySelectorAll('.chat__composer textarea').forEach(function (textarea) {
      textareas.push(textarea);
    });

    textareas.forEach(function (textarea) {
      if (textarea.dataset.autogrowEnhanced === 'true') return;
      textarea.dataset.autogrowEnhanced = 'true';

      var resize = function () {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
      };
      textarea.addEventListener('input', resize);
      textarea.addEventListener('keydown', function (event) {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
          event.preventDefault();
          textarea.form.requestSubmit();
        }
      });
      resize();
    });
  }

  function enhance(root) {
    enhanceSelects(root);
    enhanceTextareas(root);
  }

  document.addEventListener('click', function (event) {
    if (openSelect && !openSelect.wrapper.contains(event.target) && !openSelect.menu.contains(event.target)) {
      closeSelect(openSelect, false);
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && openSelect) {
      event.preventDefault();
      closeSelect(openSelect, true);
    }
  });

  window.addEventListener('resize', function () {
    positionMenu(openSelect);
  });
  document.addEventListener('scroll', function () {
    positionMenu(openSelect);
  }, true);

  document.body.addEventListener('htmx:beforeRequest', function () {
    closeSelect(openSelect, false);
  });
  document.body.addEventListener('htmx:afterRequest', function (event) {
    if (event.detail.elt && event.detail.elt.matches('#message-composer')) {
      var textarea = event.detail.elt.querySelector('textarea');
      if (textarea) {
        textarea.style.height = 'auto';
        textarea.focus();
      }
    }
  });
  document.body.addEventListener('htmx:afterSwap', function (event) {
    enhance(event.detail.target);
  });
  document.body.addEventListener('htmx:afterSettle', function (event) {
    enhance(event.detail.target);
    window.requestAnimationFrame(function () {
      enhance(event.detail.target);
    });
  });

  var selectObserver = new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      if (
        mutation.type === 'attributes' &&
        mutation.target.matches('select[data-custom-select="true"]') &&
        mutation.target.dataset.customSelectEnhanced === 'true' &&
        !mutation.target.classList.contains('custom-select__native')
      ) {
        concealNativeSelect(mutation.target);
      }

      mutation.addedNodes.forEach(function (node) {
        if (node.nodeType === Node.ELEMENT_NODE) enhance(node);
      });
      mutation.removedNodes.forEach(function (node) {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (node.isConnected) return;
        if (node._customSelect) destroySelect(node._customSelect);
        node.querySelectorAll('.custom-select').forEach(function (wrapper) {
          destroySelect(wrapper._customSelect);
        });
      });

      if (
        mutation.target &&
        mutation.target.tagName === 'SELECT' &&
        mutation.target._customSelect
      ) {
        buildOptions(mutation.target._customSelect);
        syncSelect(mutation.target._customSelect);
      }
    });
  });

  enhance(document);
  selectObserver.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class']
  });
})();
