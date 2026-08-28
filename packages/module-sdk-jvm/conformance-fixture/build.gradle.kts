// Conformance worker used by the platform's pytest suite
// (apps/api/tests/test_worker_conformance.py): a tiny module exercising every
// ctx.* surface of modules/PROTOCOL.md through the real SDK.

plugins {
    java
    application
}

dependencies {
    implementation(project(":sdk"))
}

application {
    mainClass = "mate.sdk.conformance.ConformanceWorker"
}

tasks.shadowJar {
    archiveFileName = "conformance-worker-all.jar"
    manifest {
        attributes["Main-Class"] = application.mainClass.get()
    }
}
