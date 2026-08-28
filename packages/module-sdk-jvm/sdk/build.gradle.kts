// The SDK library module authors compile against. Distributed as the shadow
// jar (`mate-sdk-jvm-<version>-all.jar`): Jackson is bundled + relocated, so
// the SDK jar is the ONLY compile-time dependency a module needs.

plugins {
    `java-library`
}

dependencies {
    implementation("com.fasterxml.jackson.core:jackson-databind:2.17.2")

    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

java {
    withSourcesJar()
}

tasks.test {
    useJUnitPlatform()
}
