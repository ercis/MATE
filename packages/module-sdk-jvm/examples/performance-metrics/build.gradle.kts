// The bundled example JVM module: event-log performance metrics.
// `copyExampleJar` drops the fat jar into modules/performance_java/dist/ - the
// committed artefact the platform actually runs (`make sdk-jvm` rebuilds it).

plugins {
    java
    application
}

dependencies {
    implementation(project(":sdk"))
}

application {
    mainClass = "mate.modules.performance.PerformanceMetricsModule"
}

tasks.shadowJar {
    archiveFileName = "performance-metrics-all.jar"
    manifest {
        attributes["Main-Class"] = application.mainClass.get()
    }
}

val copyExampleJar by tasks.registering(Copy::class) {
    dependsOn(tasks.shadowJar)
    from(tasks.shadowJar.flatMap { it.archiveFile })
    into(rootProject.layout.projectDirectory.dir("../../modules/performance_java/dist"))
}
