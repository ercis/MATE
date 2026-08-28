// Root build for the Mate JVM module SDK (see modules/PROTOCOL.md).
//
// Conventions shared by every subproject:
//   - bytecode targets Java 17 (oldest supported LTS for module authors; the
//     platform image ships a Temurin 21 JRE, which runs 17 bytecode),
//   - shadow ("fat") jars relocate Jackson to `mate.sdk.internal.jackson` so a
//     module's own Jackson (any version) can never conflict with the SDK's.

import com.github.jengelman.gradle.plugins.shadow.tasks.ShadowJar

plugins {
    java
    id("com.gradleup.shadow") version "8.3.6" apply false
}

subprojects {
    apply(plugin = "java")
    apply(plugin = "com.gradleup.shadow")

    group = "mate"
    version = "0.1.0"

    repositories {
        mavenCentral()
    }

    tasks.withType<JavaCompile>().configureEach {
        options.release = 17
        options.encoding = "UTF-8"
    }

    tasks.withType<ShadowJar>().configureEach {
        relocate("com.fasterxml.jackson", "mate.sdk.internal.jackson")
        mergeServiceFiles()
    }
}
