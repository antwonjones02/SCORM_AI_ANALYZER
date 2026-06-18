/*
 * Workplace Fire Safety Essentials — single-SCO SCORM 1.2 course.
 * Plain vanilla JS, no frameworks, no network requests.
 * Gracefully degrades to a no-op SCORM shim when no LMS API is present.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /* SCORM 1.2 API discovery (standard ADL findAPI algorithm)            */
  /* ------------------------------------------------------------------ */

  function findAPI(win) {
    var tries = 0;
    while ((win.API == null) && (win.parent != null) && (win.parent != win)) {
      tries++;
      if (tries > 500) {
        return null;
      }
      win = win.parent;
    }
    return win.API;
  }

  function getAPI() {
    var api = null;
    try {
      api = findAPI(window);
      if ((api == null) && (window.opener != null) && (typeof window.opener !== "undefined")) {
        api = findAPI(window.opener);
      }
    } catch (e) {
      api = null;
    }
    return api;
  }

  /* Thin wrapper: every call is guarded so the course never crashes
     when running outside an LMS. */
  var scorm = {
    api: null,
    connected: false,

    init: function () {
      this.api = getAPI();
      if (this.api == null) {
        return false;
      }
      try {
        var ok = this.api.LMSInitialize("");
        this.connected = (ok === "true" || ok === true);
      } catch (e) {
        this.connected = false;
      }
      return this.connected;
    },

    set: function (element, value) {
      if (!this.connected) { return "false"; }
      try {
        return this.api.LMSSetValue(element, value);
      } catch (e) {
        return "false";
      }
    },

    get: function (element) {
      if (!this.connected) { return ""; }
      try {
        return this.api.LMSGetValue(element);
      } catch (e) {
        return "";
      }
    },

    commit: function () {
      if (!this.connected) { return "false"; }
      try {
        return this.api.LMSCommit("");
      } catch (e) {
        return "false";
      }
    },

    finish: function () {
      if (!this.connected) { return "false"; }
      try {
        var r = this.api.LMSFinish("");
        this.connected = false;
        return r;
      } catch (e) {
        return "false";
      }
    }
  };

  /* ------------------------------------------------------------------ */
  /* Course state                                                        */
  /* ------------------------------------------------------------------ */

  var state = {
    screen: 0,
    interactionCount: 0,
    correctCount: 0,
    totalQuestions: 2,
    finished: false
  };

  var app = null;

  /* ------------------------------------------------------------------ */
  /* Quiz definitions                                                    */
  /* ------------------------------------------------------------------ */

  var questions = [
    {
      id: "fire-safety-q1-pass-technique",
      prompt: "When operating a portable fire extinguisher, what does the PASS technique stand for?",
      choices: [
        { key: "a", text: "Push, Activate, Spray, Stop" },
        { key: "b", text: "Pull, Aim, Squeeze, Sweep" },
        { key: "c", text: "Point, Alert, Spray, Stand" },
        { key: "d", text: "Pull, Alarm, Spray, Shut" }
      ],
      correct: "b",
      feedbackCorrect: "Correct! Pull the pin, Aim at the base of the fire, Squeeze the handle, and Sweep from side to side.",
      feedbackWrong: "Not quite. PASS stands for Pull, Aim, Squeeze, Sweep — always aim at the base of the flames."
    },
    {
      id: "fire-safety-q2-first-action",
      prompt: "You discover a small fire in the office kitchen. What should you do FIRST?",
      choices: [
        { key: "a", text: "Raise the alarm so everyone is alerted" },
        { key: "b", text: "Gather your personal belongings" },
        { key: "c", text: "Take the elevator to the ground floor" },
        { key: "d", text: "Open the windows to let smoke out" }
      ],
      correct: "a",
      note: "Scenario: it is 3:45 pm on a Tuesday. You walk into the kitchen and see flames the size of a wastepaper basket coming from a toaster on the counter. Several colleagues are working in the open-plan office next door and have not noticed anything yet.",
      feedbackCorrect: "Correct! Raising the alarm comes first — people cannot evacuate from a fire they do not know about.",
      feedbackWrong: "Not quite. Your first action is always to raise the alarm so colleagues can begin evacuating immediately."
    }
  ];

  /* ------------------------------------------------------------------ */
  /* SCORM data recording                                                */
  /* ------------------------------------------------------------------ */

  function recordInteraction(question, response, isCorrect) {
    var n = state.interactionCount;
    scorm.set("cmi.interactions." + n + ".id", question.id);
    scorm.set("cmi.interactions." + n + ".type", "choice");
    scorm.set("cmi.interactions." + n + ".student_response", response);
    scorm.set("cmi.interactions." + n + ".result", isCorrect ? "correct" : "wrong");
    state.interactionCount++;
  }

  function recordCompletion() {
    if (state.finished) { return; }
    state.finished = true;
    var percent = Math.round((state.correctCount / state.totalQuestions) * 100);
    scorm.set("cmi.core.score.min", "0");
    scorm.set("cmi.core.score.max", "100");
    scorm.set("cmi.core.score.raw", String(percent));
    scorm.set("cmi.core.lesson_status", percent >= 80 ? "passed" : "completed");
    scorm.set("cmi.core.session_time", "0000:05:30");
    scorm.commit();
    scorm.finish();
  }

  /* ------------------------------------------------------------------ */
  /* Screen rendering — each render replaces the full screen markup so   */
  /* the DOM (body.innerHTML length) changes substantially per step.     */
  /* ------------------------------------------------------------------ */

  function render(html) {
    app.innerHTML = html;
    window.scrollTo(0, 0);
  }

  function progressLabel() {
    return '<p class="progress">Screen ' + (state.screen + 1) + " of 6 &mdash; Workplace Fire Safety Essentials</p>";
  }

  function renderWelcome() {
    render(
      '<div class="screen" id="screen-welcome">' +
        progressLabel() +
        "<h1>Workplace Fire Safety Essentials</h1>" +
        "<p>Welcome to your annual fire safety training. Over the next few minutes you will " +
        "learn how fires start in the workplace, how to operate a portable fire extinguisher " +
        "safely, and what to do when the evacuation alarm sounds.</p>" +
        "<p>The course ends with a short two-question quiz. A score of 80% or higher is " +
        "required to pass. Click the button below when you are ready to begin.</p>" +
        '<button type="button" id="btn-start">Start Course</button>' +
      "</div>"
    );
    document.getElementById("btn-start").addEventListener("click", function () {
      scorm.set("cmi.core.lesson_status", "incomplete");
      scorm.commit();
      gotoScreen(1);
    });
  }

  function renderSlideHazards() {
    render(
      '<div class="screen" id="screen-hazards">' +
        progressLabel() +
        "<h2>Understanding Workplace Fire Hazards</h2>" +
        "<p>Most workplace fires are preventable. The three ingredients a fire needs &mdash; " +
        "heat, fuel, and oxygen &mdash; are present in every office, warehouse, and kitchen. " +
        "Overloaded power strips, frayed cables, and space heaters left running overnight are " +
        "the most common ignition sources in modern workplaces.</p>" +
        "<p>Fuel builds up quietly: stacked cardboard near electrical panels, paper recycling " +
        "beside photocopiers, and flammable cleaning products stored in unventilated " +
        "cupboards.</p>" +
        "<p>Report damaged equipment immediately and never block ventilation grilles on " +
        "machinery. Prevention is always safer and cheaper than response.</p>" +
        '<button type="button" id="btn-next-1">Next</button>' +
      "</div>"
    );
    document.getElementById("btn-next-1").addEventListener("click", function () {
      gotoScreen(2);
    });
  }

  function renderSlideExtinguishers() {
    render(
      '<div class="screen" id="screen-extinguishers">' +
        progressLabel() +
        "<h2>Using a Fire Extinguisher: The PASS Technique</h2>" +
        "<p>Only fight a fire when it is small, contained, and you have a clear escape route " +
        "behind you. If any of those conditions fail, evacuate immediately. Choose the right " +
        "extinguisher: water for paper and wood, CO2 for electrical equipment, and foam for " +
        "flammable liquids.</p>" +
        "<p>Operate every extinguisher with the PASS technique:</p>" +
        "<ul>" +
        "<li><strong>Pull</strong> the pin to break the tamper seal.</li>" +
        "<li><strong>Aim</strong> the nozzle low, at the base of the fire, never at the flames.</li>" +
        "<li><strong>Squeeze</strong> the handle slowly and evenly to discharge the agent.</li>" +
        "<li><strong>Sweep</strong> the nozzle from side to side until the fire is out.</li>" +
        "</ul>" +
        "<p>Most portable extinguishers discharge fully in under twenty seconds, so commit " +
        "to your escape route before you begin. Keep your back to the exit and never let the " +
        "fire move between you and the door.</p>" +
        "<p>Watch the area afterwards &mdash; fires can reignite. If it does, leave at once " +
        "and close doors behind you.</p>" +
        '<button type="button" id="btn-next-2">Next</button>' +
      "</div>"
    );
    document.getElementById("btn-next-2").addEventListener("click", function () {
      gotoScreen(3);
    });
  }

  function renderQuiz(questionIndex, screenIndex, nextScreenIndex) {
    var q = questions[questionIndex];
    var choicesHtml = "";
    for (var i = 0; i < q.choices.length; i++) {
      var c = q.choices[i];
      choicesHtml +=
        "<li><label>" +
        '<input type="radio" name="quiz-choice" value="' + c.key + '"> ' +
        c.text +
        "</label></li>";
    }
    render(
      '<div class="screen" id="screen-quiz-' + (questionIndex + 1) + '">' +
        progressLabel() +
        "<h2>Quiz &mdash; Question " + (questionIndex + 1) + " of " + state.totalQuestions + "</h2>" +
        (q.note ? '<p class="scenario"><em>' + q.note + "</em></p>" : "") +
        "<p>" + q.prompt + "</p>" +
        '<ul class="choices">' + choicesHtml + "</ul>" +
        '<div id="quiz-feedback"></div>' +
        '<button type="button" id="btn-submit">Submit</button>' +
        '<span id="next-slot"></span>' +
      "</div>"
    );

    var answered = false;
    document.getElementById("btn-submit").addEventListener("click", function () {
      if (answered) { return; }
      var selected = document.querySelector('input[name="quiz-choice"]:checked');
      var feedbackEl = document.getElementById("quiz-feedback");
      if (!selected) {
        feedbackEl.innerHTML = '<div class="feedback notice">Please select an answer before submitting.</div>';
        return;
      }
      answered = true;
      var isCorrect = (selected.value === q.correct);
      if (isCorrect) { state.correctCount++; }
      recordInteraction(q, selected.value, isCorrect);
      scorm.commit();

      feedbackEl.innerHTML = isCorrect
        ? '<div class="feedback correct">' + q.feedbackCorrect + "</div>"
        : '<div class="feedback wrong">' + q.feedbackWrong + "</div>";

      var inputs = document.querySelectorAll('input[name="quiz-choice"]');
      for (var j = 0; j < inputs.length; j++) { inputs[j].disabled = true; }
      document.getElementById("btn-submit").disabled = true;

      document.getElementById("next-slot").innerHTML =
        '<button type="button" id="btn-quiz-next">Next</button>';
      document.getElementById("btn-quiz-next").addEventListener("click", function () {
        gotoScreen(nextScreenIndex);
      });
    });
  }

  function renderComplete() {
    var percent = Math.round((state.correctCount / state.totalQuestions) * 100);
    render(
      '<div class="screen complete-banner" id="screen-complete">' +
        progressLabel() +
        "<h1>Course Complete - Congratulations!</h1>" +
        '<p class="score-line">You answered ' + state.correctCount + " of " +
        state.totalQuestions + " questions correctly &mdash; a score of <strong>" +
        percent + "%</strong>.</p>" +
        "<p>" + (percent >= 80
          ? "You have passed Workplace Fire Safety Essentials. Your result has been reported to your learning record."
          : "You have completed Workplace Fire Safety Essentials. Review the extinguisher and evacuation material and speak to your safety officer if you would like a refresher.") +
        "</p>" +
        "<p>You may now close this window.</p>" +
      "</div>"
    );
    recordCompletion();
  }

  var screens = [
    renderWelcome,
    renderSlideHazards,
    renderSlideExtinguishers,
    function () { renderQuiz(0, 3, 4); },
    function () { renderQuiz(1, 4, 5); },
    renderComplete
  ];

  function gotoScreen(index) {
    if (index < 0 || index >= screens.length) { return; }
    state.screen = index;
    screens[index]();
  }

  /* ------------------------------------------------------------------ */
  /* Boot                                                                */
  /* ------------------------------------------------------------------ */

  function boot() {
    app = document.getElementById("app");
    scorm.init(); /* no-op shim engages automatically when no API found */
    gotoScreen(0);
  }

  /* Best-effort cleanup if the learner closes the window mid-course. */
  window.addEventListener("unload", function () {
    if (!state.finished && scorm.connected) {
      scorm.commit();
      scorm.finish();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
