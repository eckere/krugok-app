(function () {
  'use strict';

  var selectSequence = 0;
  var openSelect = null;
  var dateSequence = 0;
  var openDate = null;
  var activeConfirmation = null;

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

  function padNumber(value) {
    return String(value).padStart(2, '0');
  }

  function parseDateValue(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    var match = value.trim().match(/^(\d{4})-(\d{2})-(\d{2})(?:T|\s)?(\d{2})?:?(\d{2})?/);
    if (!match) return null;
    var date = new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3]),
      Number(match[4] || 0),
      Number(match[5] || 0)
    );
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function inputDateValue(date, type, hour, minute) {
    var value = date.getFullYear() + '-' + padNumber(date.getMonth() + 1) + '-' + padNumber(date.getDate());
    if (type === 'datetime') value += 'T' + padNumber(hour) + ':' + padNumber(minute);
    return value;
  }

  function dateLabel(value, type) {
    var date = parseDateValue(value);
    if (!date) return type === 'datetime' ? 'Выберите дату и время' : 'Выберите дату';
    var label = date.toLocaleDateString('ru-RU', {
      day: 'numeric',
      month: 'long',
      year: 'numeric'
    });
    if (type === 'datetime') label += ', ' + padNumber(date.getHours()) + ':' + padNumber(date.getMinutes());
    return label;
  }

  function sameDay(left, right) {
    return Boolean(left && right) &&
      left.getFullYear() === right.getFullYear() &&
      left.getMonth() === right.getMonth() &&
      left.getDate() === right.getDate();
  }

  function syncDateTrigger(widget) {
    var hasValue = Boolean(widget.input.value);
    widget.triggerLabel.textContent = dateLabel(widget.input.value, widget.type);
    widget.trigger.classList.toggle('is-placeholder', !hasValue);
    widget.trigger.disabled = widget.input.disabled;
  }

  function syncTimeSelect(widget) {
    if (widget.type !== 'datetime') return;
    widget.hourSelect.value = padNumber(widget.hour);
    widget.minuteSelect.value = padNumber(widget.minute);
    if (widget.hourSelect._customSelect) syncSelect(widget.hourSelect._customSelect);
    if (widget.minuteSelect._customSelect) syncSelect(widget.minuteSelect._customSelect);
  }

  function renderCalendar(widget) {
    var monthStart = new Date(widget.viewDate.getFullYear(), widget.viewDate.getMonth(), 1);
    var offset = (monthStart.getDay() + 6) % 7;
    var gridStart = new Date(monthStart.getFullYear(), monthStart.getMonth(), 1 - offset);
    var today = new Date();

    widget.monthLabel.textContent = monthStart.toLocaleDateString('ru-RU', {
      month: 'long',
      year: 'numeric'
    });
    widget.days.innerHTML = '';

    for (var index = 0; index < 42; index += 1) {
      var cellDate = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + index);
      var day = document.createElement('button');
      day.type = 'button';
      day.className = 'custom-date__day';
      day.textContent = String(cellDate.getDate());
      day.setAttribute('aria-label', cellDate.toLocaleDateString('ru-RU', {
        day: 'numeric', month: 'long', year: 'numeric'
      }));

      if (cellDate.getMonth() !== monthStart.getMonth()) day.classList.add('is-outside');
      if (sameDay(cellDate, today)) day.classList.add('is-today');
      if (sameDay(cellDate, widget.selectedDate)) {
        day.classList.add('is-selected');
        day.setAttribute('aria-pressed', 'true');
      } else {
        day.setAttribute('aria-pressed', 'false');
      }

      (function (chosenDate) {
        day.addEventListener('click', function () {
          widget.selectedDate = new Date(chosenDate.getFullYear(), chosenDate.getMonth(), chosenDate.getDate());
          widget.viewDate = new Date(chosenDate.getFullYear(), chosenDate.getMonth(), 1);
          renderCalendar(widget);
        });
      })(cellDate);

      widget.days.appendChild(day);
    }
  }

  function positionDateMenu(widget) {
    if (!widget || widget.menu.hidden) return;
    var rect = widget.trigger.getBoundingClientRect();
    var gap = 9;
    var viewportGap = 12;
    var menuWidth = Math.min(356, window.innerWidth - viewportGap * 2);
    var menuHeight = widget.menu.offsetHeight;
    var spaceBelow = window.innerHeight - rect.bottom - viewportGap;
    var openAbove = spaceBelow < menuHeight && rect.top > spaceBelow;
    var left = Math.min(
      Math.max(viewportGap, rect.left),
      window.innerWidth - viewportGap - menuWidth
    );

    widget.menu.style.width = menuWidth + 'px';
    widget.menu.style.left = left + 'px';
    widget.menu.style.top = openAbove
      ? Math.max(viewportGap, rect.top - menuHeight - gap) + 'px'
      : Math.min(window.innerHeight - viewportGap - menuHeight, rect.bottom + gap) + 'px';
  }

  function closeDate(widget, restoreFocus) {
    if (!widget) return;
    if (openSelect && widget.menu.contains(openSelect.wrapper)) closeSelect(openSelect, false);
    widget.wrapper.classList.remove('is-open');
    widget.trigger.setAttribute('aria-expanded', 'false');
    widget.menu.hidden = true;
    if (openDate === widget) openDate = null;
    if (restoreFocus && widget.trigger.isConnected) widget.trigger.focus();
  }

  function openDateMenu(widget) {
    if (widget.input.disabled) return;
    if (openSelect) closeSelect(openSelect, false);
    if (openDate && openDate !== widget) closeDate(openDate, false);

    var current = parseDateValue(widget.input.value) || new Date();
    widget.selectedDate = new Date(current.getFullYear(), current.getMonth(), current.getDate());
    widget.viewDate = new Date(current.getFullYear(), current.getMonth(), 1);
    widget.hour = current.getHours();
    widget.minute = current.getMinutes();
    syncTimeSelect(widget);
    renderCalendar(widget);

    openDate = widget;
    widget.wrapper.classList.add('is-open');
    widget.trigger.setAttribute('aria-expanded', 'true');
    widget.menu.hidden = false;
    positionDateMenu(widget);
  }

  function destroyDate(widget) {
    if (!widget) return;
    if (openDate === widget) openDate = null;
    widget.menu.remove();
  }

  function accessibleInputLabel(input) {
    var value = input.getAttribute('aria-label') || '';
    if (!value && input.id) {
      Array.prototype.some.call(document.querySelectorAll('label'), function (label) {
        if (label.htmlFor !== input.id) return false;
        value = label.textContent.trim();
        return true;
      });
    }
    return value || input.name || 'Дата';
  }

  function addTimeOptions(select, count) {
    for (var index = 0; index < count; index += 1) {
      var option = document.createElement('option');
      option.value = padNumber(index);
      option.textContent = padNumber(index);
      select.appendChild(option);
    }
  }

  function enhanceDate(input) {
    if (input.dataset.customDateEnhanced === 'true') {
      if (input._customDate) syncDateTrigger(input._customDate);
      return;
    }

    dateSequence += 1;
    var type = input.dataset.customDate === 'datetime' ? 'datetime' : 'date';
    var wrapper = document.createElement('div');
    var trigger = document.createElement('button');
    var triggerLabel = document.createElement('span');
    var menu = document.createElement('div');
    var menuId = 'custom-date-menu-' + dateSequence;
    var label = accessibleInputLabel(input);

    wrapper.className = 'custom-date';
    trigger.type = 'button';
    trigger.className = 'custom-date__trigger';
    trigger.setAttribute('aria-label', label);
    trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-controls', menuId);
    triggerLabel.className = 'custom-date__value';
    trigger.appendChild(triggerLabel);

    menu.id = menuId;
    menu.className = 'custom-date__menu';
    menu.hidden = true;
    menu.setAttribute('role', 'dialog');
    menu.setAttribute('aria-label', label);
    menu.innerHTML =
      '<div class="custom-date__header">' +
        '<button type="button" class="custom-date__nav" data-date-prev aria-label="Предыдущий месяц">‹</button>' +
        '<div class="custom-date__month" aria-live="polite"></div>' +
        '<button type="button" class="custom-date__nav" data-date-next aria-label="Следующий месяц">›</button>' +
      '</div>' +
      '<div class="custom-date__weekdays" aria-hidden="true">' +
        '<span class="custom-date__weekday">Пн</span><span class="custom-date__weekday">Вт</span>' +
        '<span class="custom-date__weekday">Ср</span><span class="custom-date__weekday">Чт</span>' +
        '<span class="custom-date__weekday">Пт</span><span class="custom-date__weekday">Сб</span>' +
        '<span class="custom-date__weekday">Вс</span>' +
      '</div>' +
      '<div class="custom-date__days"></div>' +
      '<div class="custom-date__time"' + (type === 'date' ? ' hidden' : '') + '>' +
        '<label class="custom-date__time-field"><span>Часы</span><select data-date-hour data-custom-select="true" aria-label="Часы"></select></label>' +
        '<span class="custom-date__time-separator">:</span>' +
        '<label class="custom-date__time-field"><span>Минуты</span><select data-date-minute data-custom-select="true" aria-label="Минуты"></select></label>' +
      '</div>' +
      '<div class="custom-date__footer">' +
        '<div class="custom-date__footer-group"><button type="button" class="btn btn-ghost btn-sm" data-date-clear>Очистить</button></div>' +
        '<div class="custom-date__footer-group"><button type="button" class="btn btn-secondary btn-sm" data-date-today>Сегодня</button>' +
        '<button type="button" class="btn btn-primary btn-sm" data-date-apply>Готово</button></div>' +
      '</div>';

    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    wrapper.appendChild(trigger);
    document.body.appendChild(menu);

    input.classList.add('custom-date__native');
    input.setAttribute('aria-hidden', 'true');
    input.setAttribute('tabindex', '-1');
    input.dataset.customDateEnhanced = 'true';

    var current = parseDateValue(input.value) || new Date();
    var widget = {
      input: input,
      type: type,
      wrapper: wrapper,
      trigger: trigger,
      triggerLabel: triggerLabel,
      menu: menu,
      monthLabel: menu.querySelector('.custom-date__month'),
      days: menu.querySelector('.custom-date__days'),
      hourSelect: menu.querySelector('[data-date-hour]'),
      minuteSelect: menu.querySelector('[data-date-minute]'),
      selectedDate: new Date(current.getFullYear(), current.getMonth(), current.getDate()),
      viewDate: new Date(current.getFullYear(), current.getMonth(), 1),
      hour: current.getHours(),
      minute: current.getMinutes()
    };
    input._customDate = widget;
    wrapper._customDate = widget;

    addTimeOptions(widget.hourSelect, 24);
    addTimeOptions(widget.minuteSelect, 60);
    widget.hourSelect.value = padNumber(widget.hour);
    widget.minuteSelect.value = padNumber(widget.minute);
    enhanceSelects(menu);
    syncDateTrigger(widget);
    renderCalendar(widget);

    trigger.addEventListener('click', function () {
      if (menu.hidden) openDateMenu(widget);
      else closeDate(widget, false);
    });
    menu.querySelector('[data-date-prev]').addEventListener('click', function () {
      widget.viewDate = new Date(widget.viewDate.getFullYear(), widget.viewDate.getMonth() - 1, 1);
      renderCalendar(widget);
    });
    menu.querySelector('[data-date-next]').addEventListener('click', function () {
      widget.viewDate = new Date(widget.viewDate.getFullYear(), widget.viewDate.getMonth() + 1, 1);
      renderCalendar(widget);
    });
    menu.querySelector('[data-date-today]').addEventListener('click', function () {
      var now = new Date();
      widget.selectedDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      widget.viewDate = new Date(now.getFullYear(), now.getMonth(), 1);
      widget.hour = now.getHours();
      widget.minute = now.getMinutes();
      syncTimeSelect(widget);
      renderCalendar(widget);
    });
    menu.querySelector('[data-date-clear]').addEventListener('click', function () {
      input.value = '';
      syncDateTrigger(widget);
      input.dispatchEvent(new Event('change', {bubbles: true}));
      closeDate(widget, true);
    });
    menu.querySelector('[data-date-apply]').addEventListener('click', function () {
      if (!widget.selectedDate) widget.selectedDate = new Date();
      widget.hour = Number(widget.hourSelect.value || 0);
      widget.minute = Number(widget.minuteSelect.value || 0);
      input.value = inputDateValue(widget.selectedDate, widget.type, widget.hour, widget.minute);
      syncDateTrigger(widget);
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
      closeDate(widget, true);
    });
    widget.hourSelect.addEventListener('change', function () {
      widget.hour = Number(widget.hourSelect.value || 0);
    });
    widget.minuteSelect.addEventListener('change', function () {
      widget.minute = Number(widget.minuteSelect.value || 0);
    });
    input.addEventListener('change', function () {
      syncDateTrigger(widget);
    });
  }

  function enhanceDates(root) {
    var scope = root && root.querySelectorAll ? root : document;
    if (root && root.matches && root.matches('input[data-custom-date]')) enhanceDate(root);
    scope.querySelectorAll('input[data-custom-date]').forEach(enhanceDate);
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

  function enhanceSearchFields(root) {
    var scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll('.search-field').forEach(function (field) {
      if (field.dataset.searchEnhanced === 'true') return;
      var input = field.querySelector('input[type="search"]');
      var clearButton = field.querySelector('[data-search-clear]');
      if (!input || !clearButton) return;
      field.dataset.searchEnhanced = 'true';

      var syncClearButton = function () {
        clearButton.hidden = !input.value;
      };
      input.addEventListener('input', syncClearButton);
      clearButton.addEventListener('click', function () {
        input.value = '';
        syncClearButton();
        input.focus();
        input.dispatchEvent(new Event('input', {bubbles: true}));
      });
      syncClearButton();
    });
  }

  function enhance(root) {
    enhanceSelects(root);
    enhanceDates(root);
    enhanceTextareas(root);
    enhanceSearchFields(root);
  }

  function closeActionMenus(except) {
    document.querySelectorAll('.entity-actions-menu[open]').forEach(function (menu) {
      if (menu !== except) menu.removeAttribute('open');
    });
  }

  document.addEventListener('click', function (event) {
    var eventPath = typeof event.composedPath === 'function' ? event.composedPath() : [];
    if (openSelect && !openSelect.wrapper.contains(event.target) && !openSelect.menu.contains(event.target)) {
      closeSelect(openSelect, false);
    }
    if (
      openDate &&
      eventPath.indexOf(openDate.wrapper) === -1 &&
      eventPath.indexOf(openDate.menu) === -1 &&
      !event.target.closest('.custom-select__menu')
    ) {
      closeDate(openDate, false);
    }
    var actionMenu = event.target.closest('.entity-actions-menu');
    closeActionMenus(actionMenu);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Tab' && activeConfirmation && activeConfirmation.panel) {
      var confirmationControls = activeConfirmation.panel.querySelectorAll('button:not([disabled]), a[href]');
      if (confirmationControls.length) {
        var firstControl = confirmationControls[0];
        var lastControl = confirmationControls[confirmationControls.length - 1];
        if (event.shiftKey && document.activeElement === firstControl) {
          event.preventDefault();
          lastControl.focus();
        } else if (!event.shiftKey && document.activeElement === lastControl) {
          event.preventDefault();
          firstControl.focus();
        }
      }
    }
    if (event.key === 'Escape' && openSelect) {
      event.preventDefault();
      closeSelect(openSelect, true);
    }
    if (event.key === 'Escape' && openDate) {
      event.preventDefault();
      closeDate(openDate, true);
    }
    if (event.key === 'Escape' && activeConfirmation) {
      event.preventDefault();
      activeConfirmation.cancel();
    }
    if (event.key === 'Escape') closeActionMenus();
  });

  window.addEventListener('resize', function () {
    positionMenu(openSelect);
    positionDateMenu(openDate);
  });
  document.addEventListener('scroll', function () {
    positionMenu(openSelect);
    positionDateMenu(openDate);
  }, true);

  document.body.addEventListener('htmx:beforeRequest', function () {
    closeSelect(openSelect, false);
    closeDate(openDate, false);
    closeActionMenus();
  });
  document.body.addEventListener('htmx:confirm', function (event) {
    var question = event.detail && event.detail.question;
    if (!question) return;

    event.preventDefault();
    if (activeConfirmation) activeConfirmation.cancel();

    var returnFocus = event.detail.elt;
    var backdrop = document.createElement('div');
    var panel = document.createElement('section');
    var icon = document.createElement('div');
    var title = document.createElement('h2');
    var message = document.createElement('p');
    var actions = document.createElement('div');
    var cancel = document.createElement('button');
    var confirm = document.createElement('button');
    var titleId = 'custom-confirm-title-' + Date.now();

    backdrop.className = 'custom-confirm';
    panel.className = 'custom-confirm__panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', titleId);
    icon.className = 'custom-confirm__icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '!';
    title.id = titleId;
    title.className = 'custom-confirm__title';
    title.textContent = 'Подтвердите действие';
    message.className = 'custom-confirm__message';
    message.textContent = question;
    actions.className = 'custom-confirm__actions';
    cancel.type = 'button';
    cancel.className = 'btn btn-secondary';
    cancel.textContent = 'Отмена';
    confirm.type = 'button';
    confirm.className = 'btn btn-danger';
    confirm.textContent = 'Подтвердить';

    actions.appendChild(cancel);
    actions.appendChild(confirm);
    panel.appendChild(icon);
    panel.appendChild(title);
    panel.appendChild(message);
    panel.appendChild(actions);
    backdrop.appendChild(panel);
    document.body.appendChild(backdrop);

    function closeConfirmation(restoreFocus) {
      backdrop.remove();
      activeConfirmation = null;
      if (restoreFocus && returnFocus && returnFocus.isConnected) returnFocus.focus();
    }

    activeConfirmation = {
      panel: panel,
      cancel: function () {
        closeConfirmation(true);
      }
    };

    cancel.addEventListener('click', activeConfirmation.cancel);
    confirm.addEventListener('click', function () {
      closeConfirmation(false);
      event.detail.issueRequest(true);
    });
    backdrop.addEventListener('click', function (clickEvent) {
      if (clickEvent.target === backdrop) activeConfirmation.cancel();
    });
    cancel.focus();
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

  var selectObserver = new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      mutation.addedNodes.forEach(function (node) {
        if (node.nodeType === Node.ELEMENT_NODE) enhance(node);
      });
      mutation.removedNodes.forEach(function (node) {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (node.isConnected) return;
        if (node._customSelect) destroySelect(node._customSelect);
        if (node._customDate) destroyDate(node._customDate);
        node.querySelectorAll('.custom-select').forEach(function (wrapper) {
          destroySelect(wrapper._customSelect);
        });
        node.querySelectorAll('.custom-date').forEach(function (wrapper) {
          destroyDate(wrapper._customDate);
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
    subtree: true
  });
})();
